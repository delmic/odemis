# -*- coding: utf-8 -*-
from __future__ import division

import logging
import os
import time
import unittest
from concurrent.futures._base import CancelledError, FINISHED

import odemis
import odemis.acq.stream as stream
from odemis import model
from odemis.acq.acqmng import SettingsObserver
from odemis.acq.stitching._tiledacq import TiledAcquisitionTask, acquireTiledArea
from odemis.util import test
from odemis.util.comp import compute_camera_fov
from odemis.util.test import assert_pos_almost_equal

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
CRYOSECOM_CONFIG = CONFIG_PATH + "sim/cryosecom-sim.yaml"


class CRYOSECOMTestCase(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(CRYOSECOM_CONFIG)
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
        # Create 1 sem stream and 2 fm streams to be used in testing
        ss1 = stream.SEMStream("sem1", cls.sed, cls.sed.data, cls.ebeam,
                               emtvas={"dwellTime", "scale", "magnification", "pixelSize"})

        fs1 = stream.FluoStream("fluo1", cls.ccd, cls.ccd.data,
                                cls.light, cls.light_filter, focuser=cls.focus)
        fs1.excitation.value = sorted(fs1.excitation.choices)[0]

        fs2 = stream.FluoStream("fluo2", cls.ccd, cls.ccd.data,
                                cls.light, cls.light_filter)
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
        overlap = 0.2
        area = (-0.001, -0.001, 0.001, 0.001)
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=area, overlap=overlap, future=model.InstantaneousFuture())
        fov = tiled_acq_task._sfov
        # use total area as multiples of fov (in case simulator parameters changed)
        tiled_acq_task._total_area = (2 * fov[0], 2 * fov[1])
        num_tiles = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (3, 3))
        tiled_acq_task._overlap = 0
        num_tiles = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (2, 2))
        tiled_acq_task._total_area = (10 ** -6, 10 ** -6)  # smaller than fov
        num_tiles = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (1, 1))

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
        fov = (10 ** -5, 10 ** -5)
        self.stage.moveAbs({'x': -0.001, 'y': -0.001}).result()
        tiled_acq_task._moveToTile((0, 0), (0, 0), fov)
        exp_pos = {'x': -0.001, 'y': -0.001}
        assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=100e-9, match_all=False)

        tiled_acq_task._moveToTile((1, 0), (0, 0), fov)  # move right on x
        exp_pos = {'x': -0.000992}
        assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=100e-9, match_all=False)

        tiled_acq_task._moveToTile((1, 1), (1, 0), fov)  # move down on y
        exp_pos = {'x': -0.000992, 'y': -0.001008}
        assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=100e-9, match_all=False)

        tiled_acq_task._moveToTile((0, 1), (1, 1), fov)  # move back on x
        exp_pos = {'x': -0.001, 'y': -0.001008}
        assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=100e-9, match_all=False)

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

    def test_whole_procedure(self):
        """
        Test the whole procedure (acquire + stitch) of acquireTiledArea function
        """
        # With fm streams
        settings_obs = SettingsObserver([self.stage])

        fm_fov = compute_camera_fov(self.ccd)
        # Using "songbird-sim-ccd.h5" in simcam with tile max_res: (260, 348)
        area = (0, 0, fm_fov[0] * 4, fm_fov[1] * 4)  # left, top, right, bottom
        overlap = 0.2
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        future = acquireTiledArea(self.fm_streams, self.stage, area=area, overlap=overlap, settings_obs=settings_obs)
        data = future.result()
        self.assertEqual(future._state, FINISHED)
        self.assertEqual(len(data), 2)
        self.assertIsInstance(data[0], odemis.model.DataArray)
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
