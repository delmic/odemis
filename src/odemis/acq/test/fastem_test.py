# -*- coding: utf-8 -*-
"""
Created on 15th July 2021

@author: Sabrina Rossberger, Thera Pals, Éric Piel

Copyright © 2021 Sabrina Rossberger, Delmic

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
from __future__ import division

import logging
import math
import os
import time
import unittest
from concurrent.futures._base import CancelledError
from datetime import datetime

import numpy

import odemis
from odemis import model
from odemis.acq import fastem, stream
from odemis.util import test, img

# Accept three values for TEST_NOHW
# * TEST_NOHW = 1: not connected to anything => skip most of the tests
# * TEST_NOHW = sim: technolution_asm_simulator/simulator2/run_the_simulator.py
# * TEST_NOHW = 0 (or anything else): connected to the real hardware
TEST_NOHW = os.environ.get("TEST_NOHW", "0")  # Default to Hw testing
if TEST_NOHW == "sim":
    pass
elif TEST_NOHW == "0":
    TEST_NOHW = False
elif TEST_NOHW == "1":
    TEST_NOHW = True
else:
    raise ValueError("Unknown value of environment variable TEST_NOHW=%s" % TEST_NOHW)

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
FASTEM_CONFIG = CONFIG_PATH + "sim/fastem-sim.odm.yaml"


class TestFASTEMOverviewAcquisition(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            test.start_backend(FASTEM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        cls.ebeam = model.getComponent(role="e-beam")
        cls.efocuser = model.getComponent(role="ebeam-focus")
        cls.sed = model.getComponent(role="se-detector")
        cls.stage = model.getComponent(role="stage")
        cls.stage.reference({"x", "y"}).result()

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_overview_acquisition(self):
        s = stream.SEMStream("Single beam", self.sed, self.sed.data, self.ebeam,
                             focuser=self.efocuser,  # Not used during acquisition, but done by the GUI
                             hwemtvas={"scale", "dwellTime", "horizontalFoV"})
        # This should be used by the acquisition
        s.dwellTime.value = 1e-6  # s

        # These settings should be overridden by the acquisition
        s.scale.value = (2, 2)
        s.horizontalFoV.value = 20e-6  # m

        # Known position of the center scintillator
        scintillator5_area = (-0.007, -0.007, 0.007, 0.007)  # l, b, r, t
        # Small area for DEBUG (3x3)
        # scintillator5_area = (-0.002, -0.002, 0.002, 0.002)  # l, b, r, t

        est_time = fastem.estimateTiledAcquisitionTime(s, self.stage, scintillator5_area)
        # self.assertGreater(est_time, 10)  # It should take more than 10s! (expect ~5 min)
        
        before_start_t = time.time()
        f = fastem.acquireTiledArea(s, self.stage, scintillator5_area)
        time.sleep(1)
        start_t, end_t = f.get_progress()
        self.assertGreater(start_t, before_start_t)
        # self.assertGreater(end_t, time.time() + 10)  # Should report still more than 10s

        overview_da = f.result()
        self.assertGreater(overview_da.shape[0], 2000)
        self.assertGreater(overview_da.shape[1], 2000)

        # Check the final area fits the requested area, with possibly a little bit of margin
        bbox = img.getBoundingBox(overview_da)
        fov = bbox[2] - bbox[0], bbox[3] - bbox[1]
        logging.debug("Got image of size %s, with FoV %s = %s", overview_da.shape, fov, bbox)
        self.assertLessEqual(bbox[0], scintillator5_area[0])  # Left
        self.assertLessEqual(bbox[1], scintillator5_area[1])  # Bottom
        self.assertGreaterEqual(bbox[2], scintillator5_area[2])  # Right
        self.assertGreaterEqual(bbox[3], scintillator5_area[3])  # Top


@unittest.skipIf(TEST_NOHW, "No simulator running or HW present. Skip fastem ROA tests.")
class TestFastEMROA(unittest.TestCase):
    """Test region of acquisition (ROA) class methods."""

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No hardware available.")

        # get the hardware components
        cls.microscope = model.getMicroscope()
        cls.asm = model.getComponent(role="asm")
        cls.mppc = model.getComponent(role="mppc")
        cls.multibeam = model.getComponent(role="multibeam")
        cls.descanner = model.getComponent(role="descanner")
        cls.stage = model.getComponent(role="stage")  # TODO replace with stage-scan when ROA conversion method available
        cls.stage.reference({"x", "y"}).result()

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.descanner.physicalFlybackTime = 250e-6  # TODO why is this necessary??

    def test_estimate_single_field_time(self):
        """Check that the estimated time for a single field image is calculated correctly."""
        x_fields = 3
        y_fields = 4
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        coordinates = (0, 0, res_x * px_size_x * x_fields, res_y * px_size_y * y_fields)  # in m
        roc = fastem.FastEMROC("roc_name", coordinates)
        roa_name = time.strftime("test_megafield_id-%Y-%m-%d-%H-%M-%S")
        roa = fastem.FastEMROA(roa_name,
                               coordinates,
                               roc,
                               self.asm,
                               self.multibeam,
                               self.descanner,
                               self.mppc
                               )
        cell_res = self.mppc.cellCompleteResolution.value
        dwell_time = self.multibeam.dwellTime.value
        flyback = self.descanner.physicalFlybackTime  # extra time per line scan

        # calculate expected field acquisition time
        # (number of pixels per line * dwell time + flyback time) * number of lines
        estimated_field_acq_time = (cell_res[0] * dwell_time + flyback) * cell_res[1]
        # get field acquisition time
        field_acq_time = roa.estimate_single_field_time()

        self.assertAlmostEqual(estimated_field_acq_time, field_acq_time)

    def test_estimate_roa_time(self):
        """Check that the estimated time for one ROA (megafield) is calculated correctly."""
        # Use float for number of fields, in order to not end up with additional fields scanned and thus an
        # incorrectly estimated roa acquisition time.
        x_fields = 2.9
        y_fields = 3.2
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        coordinates = (0, 0, res_x * px_size_x * x_fields, res_y * px_size_y * y_fields)  # in m
        roc = fastem.FastEMROC("roc_name", coordinates)
        roa_name = time.strftime("test_megafield_id-%Y-%m-%d-%H-%M-%S")
        roa = fastem.FastEMROA(roa_name,
                               coordinates,
                               roc,
                               self.asm,
                               self.multibeam,
                               self.descanner,
                               self.mppc
                               )
        cell_res = self.mppc.cellCompleteResolution.value
        dwell_time = self.multibeam.dwellTime.value
        flyback = self.descanner.physicalFlybackTime  # extra time per line scan

        # calculate expected roa (megafield) acquisition time
        # (number of pixels per line * dwell time + flyback time) * number of lines * number of cells in x and y
        estimated_roa_acq_time = (cell_res[0] * dwell_time + flyback) * cell_res[1] \
                                 * math.ceil(x_fields) * math.ceil(y_fields)
        # get roa acquisition time
        roa_acq_time = roa.estimate_roa_time()

        self.assertAlmostEqual(estimated_roa_acq_time, roa_acq_time)

    def test_calculate_field_indices(self):
        """Check that the correct number and order of field indices is returned and that row and column are in the
        correct order."""
        x_fields = 3
        y_fields = 2
        self.multibeam.resolution.value = (6400, 6400)  # don't change
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        coordinates = (0, 0, res_x * px_size_x * x_fields, res_y * px_size_y * y_fields)  # in m, don't change
        roc = fastem.FastEMROC("roc_name", coordinates)
        roa_name = time.strftime("test_megafield_id-%Y-%m-%d-%H-%M-%S")
        roa = fastem.FastEMROA(roa_name,
                               coordinates,
                               roc,
                               self.asm,
                               self.multibeam,
                               self.descanner,
                               self.mppc
                               )

        expected_indices = [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]  # (col, row)

        field_indices = roa.calculate_field_indices()

        self.assertListEqual(expected_indices, field_indices)


@unittest.skipIf(TEST_NOHW, "No simulator running or HW present. Skip fastem acquisition tests.")
class TestFastEMAcquisition(unittest.TestCase):
    """Test multibeam acquisition."""

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No hardware available.")

        # get the hardware components
        cls.asm = model.getComponent(role="asm")
        cls.mppc = model.getComponent(role="mppc")
        cls.multibeam = model.getComponent(role="multibeam")
        cls.descanner = model.getComponent(role="descanner")
        cls.stage = model.getComponent(role="stage")  # TODO replace with stage-scan when ROA conversion method available
        cls.stage.reference({"x", "y"}).result()

    @classmethod
    def tearDownClass(cls):
        pass

    def test_acquire_ROA(self):
        """Acquire a small mega field image with ROA matching integer multiple of single field size."""
        x_fields = 2
        y_fields = 3
        self.multibeam.resolution.value = (6400, 6400)  # don't change
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        # Note: Do not change those values; calculate_field_indices handles floating point errors the same way
        # as an ROA that does not match an integer number of field indices by just adding an additional row or column
        # of field images.
        top = -0.002  # top corner coordinate of ROA in stage coordinates in meter
        left = +0.001  # left corner coordinate of ROA in stage coordinates in meter
        coordinates = (top, left,
                       top + res_x * px_size_x * x_fields,
                       left + res_y * px_size_y * y_fields)  # in m
        roc = fastem.FastEMROC("roc_name", coordinates)
        roa_name = time.strftime("test_megafield_id-%Y-%m-%d-%H-%M-%S")
        roa = fastem.FastEMROA(roa_name,
                               coordinates,
                               roc,
                               self.asm,
                               self.multibeam,
                               self.descanner,
                               self.mppc
                               )
        path_storage = os.path.join(datetime.today().strftime('%Y-%m-%d'), "test_project_megafield")
        f = fastem.acquire(roa, path_storage, self.multibeam, self.descanner, self.mppc, self.stage)
        data, e = f.result()

        self.assertIsNone(e)  # check no exceptions were returned
        # check data returned contains the correct number of field images
        self.assertEqual(len(data), x_fields * y_fields)
        self.assertIsInstance(data[(0, 0)], model.DataArray)

    def test_coverage_ROA(self):
        """Acquire a megafield (ROA), which does not match an integer multiple of fields.
        Check that the acquired ROA exceeds the requested ROA."""
        x_fields = 3
        y_fields = 4
        self.multibeam.resolution.value = (6400, 6400)  # don't change
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        # some extra pixels (< 1 field) to be added to the ROA
        x_margin, y_margin = (res_x / 10, res_y / 20)
        coordinates = (0, 0,
                       res_x * px_size_x * x_fields + x_margin * px_size_x,
                       res_y * px_size_y * y_fields + y_margin * px_size_y)  # in m
        roc = fastem.FastEMROC("roc_name", coordinates)
        roa_name = time.strftime("test_megafield_id-%Y-%m-%d-%H-%M-%S")
        roa = fastem.FastEMROA(roa_name,
                               coordinates,
                               roc,
                               self.asm,
                               self.multibeam,
                               self.descanner,
                               self.mppc
                               )
        path_storage = os.path.join(datetime.today().strftime('%Y-%m-%d'), "test_project_field_indices")
        f = fastem.acquire(roa, path_storage, self.multibeam, self.descanner, self.mppc, self.stage)
        data, e = f.result()

        self.assertIsNone(e)  # check no exceptions were returned
        # check data returned contains the correct number of field images
        # expect plus 1 field in x and y respectively
        self.assertEqual(len(data), (x_fields + 1) * (y_fields + 1))
        self.assertIsInstance(data[(0, 0)], model.DataArray)

    def test_progress_ROA(self):
        """Check if some progress is reported between the field images acquired for the ROA (megafield)."""
        x_fields = 2
        y_fields = 3
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        coordinates = (0, 0, res_x * px_size_x * x_fields, res_y * px_size_y * y_fields)  # in m
        roc = fastem.FastEMROC("roc_name", coordinates)
        roa_name = time.strftime("test_megafield_id-%Y-%m-%d-%H-%M-%S")
        roa = fastem.FastEMROA(roa_name,
                               coordinates,
                               roc,
                               self.asm,
                               self.multibeam,
                               self.descanner,
                               self.mppc
                               )
        path_storage = os.path.join(datetime.today().strftime('%Y-%m-%d'), "test_project_progress")

        self.updates = 0  # updated in callback on_progress_update

        f = fastem.acquire(roa, path_storage, self.multibeam, self.descanner, self.mppc, self.stage)
        f.add_update_callback(self.on_progress_update)  # callback executed every time f.set_progress is called
        f.add_done_callback(self.on_done)  # callback executed when f.set_result is called (via bindFuture)

        data, e = f.result()

        self.assertIsNone(e)  # check no exceptions were returned
        self.assertIsInstance(data[(0, 0)], model.DataArray)
        self.assertTrue(self.done)
        self.assertGreaterEqual(self.updates, 6)  # at least one update per field

    def test_cancel_ROA(self):
        """Test if it is possible to cancel between field images acquired for one ROA."""
        x_fields = 2
        y_fields = 3
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        coordinates = (0, 0, res_x * px_size_x * x_fields, res_y * px_size_y * y_fields)  # in m
        roc = fastem.FastEMROC("roc_name", coordinates)
        roa_name = time.strftime("test_megafield_id-%Y-%m-%d-%H-%M-%S")
        roa = fastem.FastEMROA(roa_name,
                               coordinates,
                               roc,
                               self.asm,
                               self.multibeam,
                               self.descanner,
                               self.mppc
                               )
        path_storage = os.path.join(datetime.today().strftime('%Y-%m-%d'), "test_project_cancel")

        self.end = None  # updated in callback on_progress_update
        self.updates = 0  # updated in callback on_progress_update
        self.done = False  # updated in callback on_done

        f = fastem.acquire(roa, path_storage, self.multibeam, self.descanner, self.mppc, self.stage)
        f.add_update_callback(self.on_progress_update)  # callback executed every time f.set_progress is called
        f.add_done_callback(self.on_done)  # callback executed when f.set_result is called (via bindFuture)

        time.sleep(1)  # make sure it's started
        self.assertTrue(f.running())
        f.cancel()

        self.assertRaises(CancelledError, f.result, 1)  # TODO what is the 1 for? @Eric
        self.assertGreaterEqual(self.updates, 3)  # at least one update at cancellation
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(f.cancelled())

    def test_stage_movement(self):
        """Test that the stage move corresponds to one field image (excluding over-scanned pixels)."""
        x_fields = 2
        y_fields = 3
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value

        # FIXME: It is not clear in which coordinate system the coordinates of the ROA are!!!
        # Note: the coordinates are in the stage coordinate system with role='stage' and not role='stage-scan'.
        # However, for fast em acquisitions we use stage-scan, which scans along the multiprobe axes.
        # FIXME: This test does not consider yet, that ROA coordinates need to be transformed into the
        #  correct coordinate system. Replace role='stage' with role='stage-scan' when function available.
        # Note: Do not change those values; calculate_field_indices handles floating point errors the same way
        # as an ROA that does not match an integer number of field indices by just adding an additional row or column
        # of field images.
        top = -0.002  # top corner coordinate of ROA in stage coordinates in meter
        left = +0.001  # left corner coordinate of ROA in stage coordinates in meter
        coordinates = (top, left, top + res_x * px_size_x * x_fields, left + res_y * px_size_y * y_fields)  # in m
        roc = fastem.FastEMROC("roc_name", coordinates)
        roa_name = time.strftime("test_megafield_id-%Y-%m-%d-%H-%M-%S")
        roa = fastem.FastEMROA(roa_name,
                               coordinates,
                               roc,
                               self.asm,
                               self.multibeam,
                               self.descanner,
                               self.mppc
                               )
        path_storage = os.path.join(datetime.today().strftime('%Y-%m-%d'), "test_project_stage_move")

        f = fastem.acquire(roa, path_storage, self.multibeam, self.descanner, self.mppc, self.stage)
        data, e = f.result()

        self.assertIsNone(e)  # check no exceptions were returned

        # total expected stage movement in x and y during the acquisition
        # half a field to start at center of first field image
        exp_move_x = res_x/2. * px_size_x + res_x * px_size_x * (x_fields - 1)
        exp_move_y = res_y/2. * px_size_y + res_y * px_size_y * (y_fields - 1)

        # TODO these comments are true, when stage is replaced with stage-scan
        # Move in the negative x direction, because the second field should be right of the first.
        # Move in positive y direction, because the second field should be bottom of the first.
        exp_position = (top - exp_move_x, left + exp_move_y)
        # get the last stage position (it is the center of the last field)
        cur_position = (self.stage.position.value['x'], self.stage.position.value['y'])

        # check stage position is matching expected position (Note: stage accuracy is ~ TODO fix decimal accordingly)
        numpy.testing.assert_almost_equal(exp_position, cur_position, decimal=6)

    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1


if __name__ == "__main__":
    unittest.main()
