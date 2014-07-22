# -*- coding: utf-8 -*-
"""
:created: 14 Aug 2014
:author: kimon
:copyright: © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License version 2 as published
    by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""
from __future__ import division

import logging
from odemis import model
from odemis.util import driver
import os
import subprocess
import time
import odemis
import unittest


logging.getLogger().setLevel(logging.DEBUG)

# ODEMISD_CMD = ["/usr/bin/python2", "-m", "odemis.odemisd.main"]
# -m doesn't work when run from PyDev... not entirely sure why
ODEMISD_CMD = ["/usr/bin/python2", os.path.dirname(odemis.__file__) + "/odemisd/main.py"]
ODEMISD_ARG = ["--log-level=2" , "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_LENS_CONFIG = CONFIG_PATH + "delphi-sim.odm.yaml"  # 7x7

class TestCombinedStage(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return

        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmd = ODEMISD_CMD + ODEMISD_ARG + [SECOM_LENS_CONFIG]
        # FIXME: give an informative warning when the comedi module has not been loaded
        ret = subprocess.call(cmd)

        if ret != 0:
            logging.error("Failed starting backend with '%s'", cmd)

        time.sleep(1)  # time to start

        # find components by their role
        cls.stage = model.getComponent(role="stage")
        cls.sem_stage = model.getComponent(role="sem-stage")
        cls.align = model.getComponent(role="align")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        # end the backend
        cmd = ODEMISD_CMD + ["--kill"]
        subprocess.call(cmd)
        model._core._microscope = None  # force reset of the microscope for next connection
        time.sleep(1)  # time to stop

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    # @unittest.skip("skip")
    def test_move_rel(self):
        stage = self.stage
        sem_stage = self.sem_stage
        align = self.align

        # f = stage.reference()
        # f.result()

        # no transformation
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(self, align.position.value, {"x":0, "y":0})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()

        # scaling
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (10, 10)})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":-1e-05, "y":-2e-05})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(self, align.position.value, {"x":0, "y":0})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()

        # rotation
        stage.updateMetadata({model.MD_ROTATION_COR: 1.57})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":2e-06, "y":-1e-06})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(self, align.position.value, {"x":0, "y":0})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()

        # offset
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (1e-06, 2e-06)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":0, "y":0})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(self, align.position.value, {"x":1e-06, "y":2e-06})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()

        # offset + scaling
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (1e-06, 2e-06)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (10, 10)})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":0, "y":0})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(self, align.position.value, {"x":1e-05, "y":2e-05})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()

    # @unittest.skip("skip")
    def test_move_abs(self):
        stage = self.stage
        sem_stage = self.sem_stage
        align = self.align

        # f = stage.reference()
        # f.result()

        # no transformation
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveAbs({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":-1e-06, "y":-2e-06})

        # scaling
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (10, 10)})
        f = stage.moveAbs({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":-1e-05, "y":-2e-05})

        # rotation
        stage.updateMetadata({model.MD_ROTATION_COR: 1.57})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveAbs({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":2e-06, "y":-1e-06})

        # offset
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (1e-06, 2e-06)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveAbs({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":0, "y":0})

        # offset + scaling
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (1e-06, 2e-06)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (10, 10)})
        f = stage.moveAbs({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(sem_stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(self, align.position.value, {"x":0, "y":0})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()

    def on_done(self, future):
        self.done += 1

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left
        self.updates += 1

    def assertPosAlmostEqual(self, test_case, actual, expected, *args, **kwargs):
        """
        Asserts that two stage positions have almost equal coordinates.
        """
        try:
            if expected.viewkeys() != actual.viewkeys():
                raise AssertionError("Dimensions of coordinates do not match")
            for dim_exp, dim_act in zip(expected.keys(), actual.keys()):
                test_case.assertAlmostEqual(actual[dim_act], expected[dim_exp])
        except AssertionError as exc:
            raise AssertionError(exc.message)

if __name__ == "__main__":
    unittest.main()
