# -*- coding: utf-8 -*-
"""
@author: Bassim Lazem

Copyright © 2020 Bassim Lazem, Delmic

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

from concurrent.futures._base import CancelledError, FINISHED
import logging
import numpy
import odemis
from odemis import model
from odemis.acq import stream
from odemis.acq.acqmng import SettingsObserver
from odemis.acq.stitching import WEAVER_COLLAGE_REVERSE, REGISTER_IDENTITY, \
    WEAVER_MEAN, acquireTiledArea
from odemis.acq.stitching._tiledacq import TiledAcquisitionTask
from odemis.util import test, img
from odemis.util.comp import compute_camera_fov, compute_scanner_fov
from odemis.util.test import assert_pos_almost_equal
import os
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
ENZEL_CONFIG = CONFIG_PATH + "sim/enzel-sim.odm.yaml"


class CRYOSECOMTestCase(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(ENZEL_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # create some streams connected to the backend
        cls.microscope = model.getMicroscope()
        cls.ccd = model.getComponent(role="ccd")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.light = model.getComponent(role="light")
        cls.focus = model.getComponent(role="focus")
        cls.light_filter = model.getComponent(role="filter")
        cls.stage = model.getComponent(role="stage")
        # Make sure the lens is referenced
        cls.focus.reference({'z'}).result()
        # The 5DoF stage is not referenced automatically, so let's do it now
        stage_axes = set(cls.stage.axes.keys())
        cls.stage.reference(stage_axes).result()
        # Create 1 sem stream and 2 fm streams to be used in testing
        ss1 = stream.SEMStream("sem1", cls.sed, cls.sed.data, cls.ebeam,
                               emtvas={"dwellTime", "scale", "magnification", "pixelSize"})

        cls.ccd.exposureTime.value = cls.ccd.exposureTime.range[0]  # go fast
        fs1 = stream.FluoStream("fluo1", cls.ccd, cls.ccd.data,
                                cls.light, cls.light_filter, focuser=cls.focus)
        fs1.excitation.value = sorted(fs1.excitation.choices)[0]

        fs2 = stream.FluoStream("fluo2", cls.ccd, cls.ccd.data,
                                cls.light, cls.light_filter, focuser=cls.focus)
        fs2.excitation.value = sorted(fs2.excitation.choices)[-1]
        cls.sem_streams = [ss1]
        cls.fm_streams = [fs1, fs2]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_get_number_of_tiles(self):
        """
        Test get number of tiles using different values of total area, fov and overlap
        :return:
        """
        fov = compute_camera_fov(self.ccd)

        # use area as multiples of fov (in case simulator parameters changed)
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, 2 * fov[0], 2 * fov[1]),
                                              overlap=0.2, future=model.InstantaneousFuture())
        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (3, 3))

        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, 2 * fov[0], 2 * fov[1]),
                                              overlap=0, future=model.InstantaneousFuture())
        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (2, 2))

        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, fov[0] / 2, fov[1] / 2),  # smaller than fov
                                              overlap=0, future=model.InstantaneousFuture())

        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (1, 1))

        # Precisely 1 x 2 FoV => should give 1 x 2 tiles
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, fov[0], 2 * fov[1]),
                                              overlap=0, future=model.InstantaneousFuture())
        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (1, 2))

    def test_generate_indices(self):
        """
        Test output of X, Y position indices scanning order
        """
        area = (-0.001, -0.001, 0.001, 0.001)
        overlap = 0.2
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=area, overlap=overlap, future=model.InstantaneousFuture())
        gen = tiled_acq_task._generateScanningIndices((0, 0))
        self.assertEqual(list(gen), [])

        gen = tiled_acq_task._generateScanningIndices((1, 1))
        res_gen = [(0, 0)]
        self.assertEqual(list(gen), res_gen)

        gen = tiled_acq_task._generateScanningIndices((2, 2))
        res_gen = [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.assertEqual(list(gen), res_gen)

        gen = list(tiled_acq_task._generateScanningIndices((2, 4)))
        res_gen = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 2), (1, 2), (1, 3), (0, 3)]
        self.assertEqual(list(gen), res_gen)

    def test_move_to_tiles(self):
        """
        Test moving the stage to a tile based on its index
        """
        area = (-0.001, -0.001, 0.001, 0.001)
        overlap = 0.2
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=area, overlap=overlap, future=model.InstantaneousFuture())
        fov = compute_camera_fov(self.ccd)
        exp_shift = fov[0] * (1 - overlap), fov[1] * (1 - overlap)
        # move to starting position (left, top)
        starting_pos = tiled_acq_task._starting_pos
        self.stage.moveAbs(starting_pos).result()
        logging.debug("Starting position: %s", starting_pos)
        # no change in movement
        tiled_acq_task._moveToTile((0, 0), (0, 0), fov)
        assert_pos_almost_equal(self.stage.position.value, starting_pos, atol=100e-9, match_all=False)

        # Note that we cannot predict precisely, as the algorithm may choose to spread
        # more or less the tiles to fit within the area.
        tiled_acq_task._moveToTile((1, 0), (0, 0), fov)  # move right on x
        exp_pos = {'x': starting_pos["x"] + exp_shift[0] / 2}
        assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=10e-6, match_all=False)

        tiled_acq_task._moveToTile((1, 1), (1, 0), fov)  # move down on y
        exp_pos = {'x': starting_pos["x"] + exp_shift[0] / 2,
                   'y': starting_pos["y"] - exp_shift[1] / 2}
        assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=10e-6, match_all=False)

        tiled_acq_task._moveToTile((0, 1), (1, 1), fov)  # move back on x
        exp_pos = {'x': starting_pos["x"],
                   'y': starting_pos["y"] - exp_shift[1] / 2}
        assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=10e-6, match_all=False)

    def test_get_fov(self):
        """
        Test getting the fov for sem and fm streams
        """
        area = (-0.001, -0.001, 0.001, 0.001)
        overlap = 0.2
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=area, overlap=overlap, future=model.InstantaneousFuture())
        sem_fov = tiled_acq_task._getFov(self.sem_streams[0])

        exp_sem_fov = (self.ebeam.shape[0] * self.ebeam.pixelSize.value[0],
                       self.ebeam.shape[1] * self.ebeam.pixelSize.value[1])
        self.assertEqual(len(sem_fov), 2)  # (float, float)
        (self.assertAlmostEqual(x, y) for x, y in zip(sem_fov, exp_sem_fov))

        fm_fov = tiled_acq_task._getFov(self.fm_streams[0])
        self.assertEqual(len(fm_fov), 2)
        pixel_size = self.ccd.getMetadata()[model.MD_PIXEL_SIZE]
        exp_fm_fov = (self.ccd.shape[0] * pixel_size[0],
                      self.ccd.shape[1] * pixel_size[1])
        (self.assertAlmostEqual(x, y) for x, y in zip(fm_fov, exp_fm_fov))

        with self.assertRaises(TypeError):
            tiled_acq_task._getFov(None)

    def test_area(self):
        """
        Test the acquired area matches the requested area
        """
        fm_fov = compute_camera_fov(self.ccd)
        # Using "songbird-sim-ccd.h5" in simcam with tile max_res: (260, 348)
        area = (0, 0, fm_fov[0] * 2, fm_fov[1] * 3)  # left, bottom, right, top
        overlap = 0.2

        # No focuser, to make it faster, and it doesn't affect the FoV
        fs = stream.FluoStream("fluo1", self.ccd, self.ccd.data, self.light, self.light_filter)
        future = acquireTiledArea([fs], self.stage, area=area, overlap=overlap,
                                  registrar=REGISTER_IDENTITY, weaver=WEAVER_MEAN)
        data = future.result()
        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data[0].shape), 2)

        # The center should be almost precisely at the center of the request RoA,
        # modulo the stage precision (which is very good on the simulator).
        # The size can be a little bit bigger (but never smaller), as it's
        # rounded up to a tile.
        bbox = img.getBoundingBox(data[0])
        data_center = data[0].metadata[model.MD_POS]
        area_center = (area[0] + area[2]) / 2, (area[1] + area[3]) / 2
        logging.debug("Expected area: %s %s, actual area: %s %s", area, area_center, bbox, data_center)
        self.assertAlmostEqual(data_center[0], area_center[0], delta=1e-6)  # +- 1µm
        self.assertAlmostEqual(data_center[1], area_center[1], delta=1e-6)  # +- 1µm

        self.assertTrue(area[0] - fm_fov[0] / 2 <= bbox[0] <= area[0])
        self.assertTrue(area[1] - fm_fov[1] / 2 <= bbox[1] <= area[1])
        self.assertTrue(area[2] <= bbox[2] <= area[2] + fm_fov[0] / 2)
        self.assertTrue(area[3] <= bbox[3] <= area[3] + fm_fov[1] / 2)

    def test_compressed_stack(self):
        """
       Test the whole procedure (acquire compressed zstack + stitch) of acquireTiledArea function
       """
        # With fm streams
        settings_obs = SettingsObserver([self.stage])
        fm_fov = compute_camera_fov(self.ccd)
        # Using "songbird-sim-ccd.h5" in simcam with tile max_res: (260, 348)
        area = (0, 0, fm_fov[0] * 2, fm_fov[1] * 2)  # left, top, right, bottom
        overlap = 0.2
        focus_value = self.focus.position.value['z']
        zsteps = 3
        # Create focus zlevels from the given zsteps number
        zlevels = numpy.linspace(focus_value - (zsteps / 2 * 1e-6), focus_value + (zsteps / 2 * 1e-6), zsteps).tolist()

        future = acquireTiledArea(self.fm_streams, self.stage, area=area, overlap=overlap, settings_obs=settings_obs, zlevels=zlevels)
        data = future.result()
        self.assertTrue(future.done())
        self.assertEqual(len(data), 2)
        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data[0].shape), 2)

    def test_whole_procedure(self):
        """
        Test the whole procedure (acquire + stitch) of acquireTiledArea function
        """
        # With fm streams
        settings_obs = SettingsObserver([self.stage])

        fm_fov = compute_camera_fov(self.ccd)
        # Using "songbird-sim-ccd.h5" in simcam with tile max_res: (260, 348)
        area = (0, 0, fm_fov[0] * 4, fm_fov[1] * 4)  # left, bottom, right, top
        overlap = 0.2
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        future = acquireTiledArea(self.fm_streams, self.stage, area=area, overlap=overlap,
                                  settings_obs=settings_obs, weaver=WEAVER_COLLAGE_REVERSE)
        data = future.result()
        self.assertEqual(future._state, FINISHED)
        self.assertEqual(len(data), 2)
        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data[0].shape), 2)

        # With sem stream
        area = (0, 0, 0.00001, 0.00001)
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        future = acquireTiledArea(self.sem_streams, self.stage, area=area, overlap=overlap)
        data = future.result()
        self.assertEqual(future._state, FINISHED)
        self.assertEqual(len(data), 1)
        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data[0].shape), 2)

    def test_registrar_weaver(self):

        overlap = 0.05  # Little overlap, no registration
        sem_fov = compute_scanner_fov(self.ebeam)
        area = (0, 0, sem_fov[0], sem_fov[1])
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        future = acquireTiledArea(self.sem_streams, self.stage, area=area,
                                  overlap=overlap, registrar=REGISTER_IDENTITY, weaver=WEAVER_MEAN)
        data = future.result()
        self.assertEqual(future._state, FINISHED)
        self.assertEqual(len(data), 1)
        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data[0].shape), 2)

    def test_progress(self):
        """
       Test progress update of acquireTiledArea function
        """
        self.start = None
        self.end = None
        self.updates = 0
        area = (0, 0, 0.00001, 0.00001)  # left, top, right, bottom
        overlap = 0.2
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        f = acquireTiledArea(self.sem_streams, self.stage, area=area, overlap=overlap)

        f.add_update_callback(self.on_progress_update)

        data = f.result()
        self.assertIsInstance(data[0], model.DataArray)
        self.assertGreaterEqual(self.updates, 2)  # at least one update per stream

    def test_cancel(self):
        """
        Test cancelling of acquireTiledArea function
        """
        self.start = None
        self.end = None
        self.updates = 0
        self.done = False
        # Get area from stage metadata
        area = (-0.0001, -0.0001, 0.0001, 0.0001)
        overlap = 0.2
        f = acquireTiledArea(self.fm_streams, self.stage, area=area, overlap=overlap)
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        time.sleep(1)  # make sure it's started
        self.assertTrue(f.running())
        f.cancel()

        self.assertRaises(CancelledError, f.result, 1)
        self.assertGreaterEqual(self.updates, 1)  # at least one update at cancellation
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(f.cancelled())

    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1


if __name__ == '__main__':
    unittest.main()
