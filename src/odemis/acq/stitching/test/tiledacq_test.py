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
import logging
import math
import os
import time
import unittest
from concurrent.futures._base import CancelledError, FINISHED

import numpy

import odemis
from odemis import model
from odemis.acq import stream
from odemis.acq.acqmng import SettingsObserver
from odemis.acq.stitching import WEAVER_COLLAGE_REVERSE, REGISTER_IDENTITY, \
    WEAVER_MEAN, acquireTiledArea, FocusingMethod
from odemis.acq.stitching._tiledacq import (TiledAcquisitionTask, get_fov, get_zstack_levels, clip_tiling_bbox_to_range,
                                            get_stream_based_bbox, get_tiled_bboxes, get_fov_based_bbox)
from odemis.util import testing, img
from odemis.util.comp import compute_camera_fov, compute_scanner_fov

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
ENZEL_CONFIG = CONFIG_PATH + "sim/enzel-sim.odm.yaml"
METEOR_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"


class CRYOSECOMTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        testing.start_backend(ENZEL_CONFIG)

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

        # Create 1 SEM stream (no focus) and 2 FM streams (with focus) to be used in testing
        ss1 = stream.SEMStream("sem1", cls.sed, cls.sed.data, cls.ebeam,
                               emtvas={"dwellTime", "scale", "magnification", "pixelSize"})

        cls.ccd.exposureTime.value = 0.1  # s, go fast (but not too fast, to still get some signal)
        fs1 = stream.FluoStream("fluo1", cls.ccd, cls.ccd.data,
                                cls.light, cls.light_filter, focuser=cls.focus)
        fs1.excitation.value = sorted(fs1.excitation.choices)[0]

        fs2 = stream.FluoStream("fluo2", cls.ccd, cls.ccd.data,
                                cls.light, cls.light_filter, focuser=cls.focus)
        fs2.excitation.value = sorted(fs2.excitation.choices)[-1]
        cls.sem_streams = [ss1]
        cls.fm_streams = [fs1, fs2]

    def setUp(self):
        # Make sure we start in focus position (easy with the simulator!)
        focus_active_pos = self.focus.getMetadata()[model.MD_FAV_POS_ACTIVE]
        self.focus.moveAbsSync(focus_active_pos)

    def test_get_number_of_tiles(self):
        """
        Test get number of tiles using different values of total area, fov and overlap
        :return:
        """
        fov = compute_camera_fov(self.ccd)
        # use area as multiples of fov (in case simulator parameters changed)

        # Smaller than FoV => 1x1
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, fov[0] / 2, fov[1] / 2),
                                              overlap=0, future=model.InstantaneousFuture())

        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (1, 1))

        # Precisely 2x2 FoV, without overlap => 2x2 tiles
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, 2 * fov[0], 2 * fov[1]),
                                              overlap=0, future=model.InstantaneousFuture())
        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (2, 2))

        # Precisely 1 x 2 FoV, without overlap => should give 1 x 2 tiles
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, fov[0], 2 * fov[1]),
                                              overlap=0, future=model.InstantaneousFuture())
        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (1, 2))

        # Precisely 0.8 FoV, with overlap 0.2 => 1x1 tiles
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, 0.8 * fov[0], 0.8 * fov[1]),
                                              overlap=0.2, future=model.InstantaneousFuture())
        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (1, 1))

        # 2x3 FoV with overlap 0.2 => 3x4
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, 2 * fov[0], 3 * fov[1]),
                                              overlap=0.2, future=model.InstantaneousFuture())
        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (3, 4))

        # Precisely 4*0.8x7*0.8 FoV, with overlap 0.2 => 4x7 tiles
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, 4 * 0.8 * fov[0], 7 * 0.8 * fov[1]),
                                              overlap=0.2, future=model.InstantaneousFuture())
        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (4, 7))

        # A tiny bit more 4*0.8x7*0.8 FoV, with overlap 0.2 => 4x7 tiles
        eps = 1e-12
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=(0, 0, 4 * 0.8 * fov[0] + eps, 7 * 0.8 * fov[1] + eps),
                                              overlap=0.2, future=model.InstantaneousFuture())
        num_tiles, starting_pos = tiled_acq_task._getNumberOfTiles()
        self.assertEqual(num_tiles, (4, 7))

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
        testing.assert_pos_almost_equal(self.stage.position.value, starting_pos, atol=100e-9, match_all=False)

        # Note that we cannot predict precisely, as the algorithm may choose to spread
        # more or less the tiles to fit within the area.
        tiled_acq_task._moveToTile((1, 0), (0, 0), fov)  # move right on x
        exp_pos = {'x': starting_pos["x"] + exp_shift[0] / 2}
        testing.assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=10e-6, match_all=False)

        tiled_acq_task._moveToTile((1, 1), (1, 0), fov)  # move down on y
        exp_pos = {'x': starting_pos["x"] + exp_shift[0] / 2,
                   'y': starting_pos["y"] - exp_shift[1] / 2}
        testing.assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=10e-6, match_all=False)

        tiled_acq_task._moveToTile((0, 1), (1, 1), fov)  # move back on x
        exp_pos = {'x': starting_pos["x"],
                   'y': starting_pos["y"] - exp_shift[1] / 2}
        testing.assert_pos_almost_equal(self.stage.position.value, exp_pos, atol=10e-6, match_all=False)

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
        settings_obs = SettingsObserver(self.microscope, [self.stage])
        fm_fov = compute_camera_fov(self.ccd)
        # Using "songbird-sim-ccd.h5" in simcam with tile max_res: (260, 348)
        area = (0, 0, fm_fov[0] * 2, fm_fov[1] * 2)  # left, top, right, bottom
        overlap = 0.2
        focus_value = self.focus.position.value['z']
        zsteps = 3
        # Create focus zlevels from the given zsteps number
        zlevels = numpy.linspace(focus_value - (zsteps / 2 * 1e-6), focus_value + (zsteps / 2 * 1e-6), zsteps).tolist()

        future = acquireTiledArea(self.fm_streams, self.stage, area=area, overlap=overlap,
                                  settings_obs=settings_obs, zlevels=zlevels,
                                  focusing_method=FocusingMethod.MAX_INTENSITY_PROJECTION)
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
        settings_obs = SettingsObserver(self.microscope, [self.stage])

        fm_fov = compute_camera_fov(self.ccd)
        # Using "songbird-sim-ccd.h5" in simcam with tile max_res: (260, 348)
        area = (0, 0, fm_fov[0] * 4, fm_fov[1] * 4)  # left, bottom, right, top
        overlap = 0.2
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        future = acquireTiledArea(self.fm_streams, self.stage, area=area, overlap=overlap,
                                  settings_obs=settings_obs, weaver=WEAVER_COLLAGE_REVERSE,
                                  focusing_method=FocusingMethod.ON_LOW_FOCUS_LEVEL)
        data = future.result()
        self.assertTrue(future.done())
        self.assertEqual(len(data), 2)
        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data[0].shape), 2)

        # With sem stream
        area = (0, 0, 0.00001, 0.00001)
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        future = acquireTiledArea(self.sem_streams, self.stage, area=area, overlap=overlap)
        data = future.result()
        self.assertTrue(future.done())
        self.assertEqual(len(data), 1)
        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data[0].shape), 2)

    def test_refocus(self):
        """Test the range in refocus function which provides the z levels for the zstack."""
        area = (-0.001, -0.001, 0.001, 0.001)
        overlap = 0.2
        focus_points = [[-0.0001989, -0.0001485, 2.519e-05],
                        [0.0, -0.0001485, -1.815e-05],
                        [0.0001989, -0.0001485, -3.383e-06],
                        [-0.0001989, 0.0, 1.38e-05],
                        [0.0, 0.0, 2.321e-06],
                        [0.0001989, 0.0, 3e-05],
                        [-0.0001989, 0.0001485, -1.781e-05],
                        [0.0, 0.0001485, -8.125e-06],
                        [0.0001989, 0.0001485, 4.766e-06]]
        zlevels = [focus_points[0][2], focus_points[1][2], focus_points[2][2]]
        axis_range = self.focus.axes['z'].range

        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=area, overlap=overlap, future=model.InstantaneousFuture(),
                                              zlevels=zlevels, focus_points=focus_points,
                                              focusing_method=FocusingMethod.MAX_INTENSITY_PROJECTION)
        tiled_acq_task._refocus()
        # min with none values
        zmin = min(axis_range)
        zmax = max(axis_range)
        # Test the min and max range of the zstack
        self.assertGreaterEqual(tiled_acq_task._zlevels[0], zmin)
        self.assertLessEqual(tiled_acq_task._zlevels[-1], zmax)

        # Test when z level is provided and the focusing method is not max intensity projection

        with self.assertRaises(NotImplementedError):
            tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                                  area=area, overlap=overlap, future=model.InstantaneousFuture(),
                                                  zlevels=zlevels, focus_points=focus_points,
                                                  focusing_method=FocusingMethod.ON_LOW_FOCUS_LEVEL)

        # Test 3 when z level, focus_points are provided and the focusing method is max intensity projection,
        # no exception should be raised
        try:
            tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                                  area=area, overlap=overlap, future=model.InstantaneousFuture(),
                                                  zlevels=zlevels, focus_points=focus_points,
                                                  focusing_method=FocusingMethod.MAX_INTENSITY_PROJECTION)
        except Exception as e:
            self.fail("Unexpected exception raised: %s" % e)

    def test_get_z_on_focus_plane(self):
        """Test the function that calculates the z position on the focus plane."""
        area = (-0.001, -0.001, 0.001, 0.001)
        overlap = 0.2
        focus_points = [[-0.0001989, -0.0001485, 2.519e-05],
                        [0.0, -0.0001485, -1.815e-05],
                        [0.0001989, -0.0001485, -3.383e-06],
                        [-0.0001989, 0.0, 1.38e-05],
                        [0.0, 0.0, 2.321e-06],
                        [0.0001989, 0.0, 3e-05],
                        [-0.0001989, 0.0001485, -1.781e-05],
                        [0.0, 0.0001485, -8.125e-06],
                        [0.0001989, 0.0001485, 4.766e-06]]

        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=area, overlap=overlap, future=model.InstantaneousFuture(),
                                              focus_points=focus_points)
        # Test a point that is on the plane
        x = 0
        y = 0
        z = tiled_acq_task._get_z_on_focus_plane(x, y)
        self.assertEqual(z, tiled_acq_task._focus_plane["gamma"])

        # Check the equation of the plane for the given focus points. Plane equation is found from the focus points
        gamma = 3.1784e-06
        normal = (0.008549, -0.02786, -1)
        z = tiled_acq_task._get_z_on_focus_plane(x, y)
        self.assertAlmostEqual(tiled_acq_task._focus_plane["gamma"], gamma)
        self.assertAlmostEqual(z, gamma + normal[0] * x + normal[1] * y)

    def test_get_triangulated_focus_point(self):
        """Test the function that calculates the z position of a point in the focus plane."""
        n_tiles = (3, 3)
        overlap = 0.2
        area = (-0.001, -0.001, 0.001, 0.001)

        # Assumes the stage is referenced
        init_pos = (self.stage.position.value["x"], self.stage.position.value["y"])
        width, height = self.ccd.shape[:2]
        px_size = self.ccd.getMetadata().get(model.MD_PIXEL_SIZE)

        xmin = init_pos[0] - (1 - overlap) * n_tiles[0] / 2 * px_size[0] * width
        ymin = init_pos[1] - (1 - overlap) * n_tiles[1] / 2 * px_size[1] * height
        xmax = init_pos[0] + (1 - overlap) * n_tiles[0] / 2 * px_size[0] * width
        ymax = init_pos[1] + (1 - overlap) * n_tiles[1] / 2 * px_size[1] * height

        # create mesh of 9 points with above coordinates separated by n_tiles
        x = numpy.linspace(xmin, xmax, n_tiles[0])
        y = numpy.linspace(ymin, ymax, n_tiles[1])
        xv, yv = numpy.meshgrid(x, y)
        # create a list of coordinates from above
        point_2d = numpy.vstack((xv.flatten(), yv.flatten())).T

        focus_points = [[-0.0001989, -0.0001485, 2.519e-05],
                        [0.0, -0.0001485, -1.815e-05],
                        [0.0001989, -0.0001485, -3.383e-06],
                        [-0.0001989, 0.0, 1.38e-05],
                        [0.0, 0.0, 2.321e-06],
                        [0.0001989, 0.0, 3e-05],
                        [-0.0001989, 0.0001485, -1.781e-05],
                        [0.0, 0.0001485, -8.125e-06],
                        [0.0001989, 0.0001485, 4.766e-06]]
        # don't know if area and focus points are related
        tiled_acq_task = TiledAcquisitionTask(self.fm_streams, self.stage,
                                              area=area, overlap=overlap,
                                              future=model.InstantaneousFuture(), focus_points=focus_points)

        # Test a point which is inside the triangulation area
        point_inside = focus_points[0]
        z = tiled_acq_task._get_triangulated_focus_point(point_inside[0], point_inside[1])
        self.assertAlmostEqual(z, point_inside[2], places=9)

        # Test a point which is outside the triangulation area
        focus_points = numpy.array(focus_points)
        point_outside = focus_points[0] - (focus_points[1] - focus_points[0])
        z = tiled_acq_task._get_triangulated_focus_point(point_outside[0], point_outside[1])
        z_expected = tiled_acq_task._get_z_on_focus_plane(point_outside[0], point_outside[1])
        self.assertAlmostEqual(z, z_expected, places=9)

    def test_always_focusing_method(self):
        """
        Test the focusing methods ALWAYS
        """
        settings_obs = SettingsObserver(self.microscope, [self.stage])
        self._focuser_pos = []  # List of focuser positions
        # Note: we assume it doesn't randomly changes if not explicitly moving,
        # which is normally correct on the simulator.
        self.focus.position.subscribe(self._position_listener)

        # With FM streams: focuser should have moved (a lot)
        fm_fov = compute_camera_fov(self.ccd)
        # Using "songbird-sim-ccd.h5" in simcam with tile max_res: (260, 348)
        area = (0, 0, fm_fov[0] * 2, fm_fov[1] * 1.5)  # left, bottom, right, top
        overlap = 0.2
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        future = acquireTiledArea(self.fm_streams, self.stage, area=area, overlap=overlap,
                                  settings_obs=settings_obs, weaver=WEAVER_COLLAGE_REVERSE,
                                  focusing_method=FocusingMethod.ALWAYS)
        data = future.result()
        self.assertEqual(len(data), 2)
        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data[0].shape), 2)
        self.assertGreaterEqual(len(self._focuser_pos), 10)  # Typically, it should have moved a lot, like 100x

        # With SEM stream: no focuser, so normal acquisition
        self._focuser_pos = []  # reset
        sem_fov = compute_scanner_fov(self.ebeam)
        area = (0, 0, sem_fov[0] * 2, sem_fov[1] * 1.5)
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        future = acquireTiledArea(self.sem_streams, self.stage, area=area, overlap=overlap,
                                  settings_obs=settings_obs, weaver=WEAVER_MEAN,
                                  focusing_method=FocusingMethod.ALWAYS)
        data = future.result()
        self.assertTrue(future.done())

        # It shouldn't have moved, so it should be very few positions
        self.focus.position.unsubscribe(self._position_listener)
        self.assertLess(len(self._focuser_pos), 2)

    def test_estimate_time(self):
        """Test the computation of tiled acquisition time according to the observed time in real-time acquisitions
        observed in meteor/fm imaging."""
        area = (-0.001, -0.001, 0.001, 0.001)
        overlap = 0.2
        # Test with more than one z level
        tiled_acq_task = TiledAcquisitionTask([self.fm_streams[0]], self.stage, area=area, overlap=overlap,
                                              focusing_method=FocusingMethod.MAX_INTENSITY_PROJECTION,
                                              zlevels=[1, 2, 3])
        tiled_acq_task._nx = 7
        tiled_acq_task._ny = 7
        # observed time duration during stage movement between tiles
        min_stage_time = tiled_acq_task._nx * tiled_acq_task._ny * 0.3
        # observed time duration during tile acquisition
        min_ties_acq = tiled_acq_task._nx * tiled_acq_task._ny * len(tiled_acq_task._zlevels)
        min_total_time = min_stage_time + min_ties_acq
        # overhead of 30% on min_total_time is used to account for stream settings, like, exposure time etc.
        self.assertTrue(min_total_time <= tiled_acq_task.estimateTime() <= 1.30 * min_total_time)
        # Test with one z level
        tiled_acq_task = TiledAcquisitionTask([self.fm_streams[0]], self.stage, area=area, overlap=overlap,
                                              focusing_method=FocusingMethod.MAX_INTENSITY_PROJECTION,
                                              zlevels=[1])
        tiled_acq_task._nx = 10
        tiled_acq_task._ny = 10
        # observed time duration during stage movement between tiles
        min_stage_time = tiled_acq_task._nx * tiled_acq_task._ny * 0.3
        # observed time duration during tile acquisition
        min_ties_acq = tiled_acq_task._nx * tiled_acq_task._ny * len(tiled_acq_task._zlevels)
        min_total_time = min_stage_time + min_ties_acq
        # overhead of 30% on min_total_time is used to account for stream settings, like, exposure time etc.
        self.assertTrue(min_total_time <= tiled_acq_task.estimateTime() <= 1.30 * min_total_time)

    def _position_listener(self, pos):
        self._focuser_pos.append(pos)

    def test_registrar_weaver(self):
        overlap = 0.05  # Little overlap, no registration
        sem_fov = compute_scanner_fov(self.ebeam)
        area = (0, 0, sem_fov[0], sem_fov[1])
        self.stage.moveAbs({'x': 0, 'y': 0}).result()
        future = acquireTiledArea(self.sem_streams, self.stage, area=area,
                                  overlap=overlap, registrar=REGISTER_IDENTITY, weaver=WEAVER_MEAN,
                                  focusing_method=FocusingMethod.NONE)
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

class TiledAcqUtilTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        testing.start_backend(METEOR_CONFIG)

        # create some streams connected to the backend
        cls.microscope = model.getMicroscope()
        cls.ccd = model.getComponent(role="ccd")
        cls.light = model.getComponent(role="light")
        cls.focus = model.getComponent(role="focus")
        cls.light_filter = model.getComponent(role="filter")
        cls.stage = model.getComponent(role="stage")
        cls.stage_bare = model.getComponent(role="stage-bare")

        # Make sure the lens is referenced
        cls.focus.reference({'z'}).result()
        # The 5DoF stage is not referenced automatically, so let's do it now
        stage_axes = set(cls.stage.axes.keys())
        cls.stage.reference(stage_axes).result()

        cls.ccd.exposureTime.value = 0.1  # s, go fast (but not too fast, to still get some signal)
        fs1 = stream.FluoStream("fluo1", cls.ccd, cls.ccd.data,
                                cls.light, cls.light_filter, focuser=cls.focus)
        fs1.excitation.value = sorted(fs1.excitation.choices)[0]

        fs2 = stream.FluoStream("fluo2", cls.ccd, cls.ccd.data,
                                cls.light, cls.light_filter, focuser=cls.focus)
        fs2.excitation.value = sorted(fs2.excitation.choices)[-1]
        cls.fm_streams = [fs1, fs2]

    def setUp(self):
        # Make sure we start in focus position (easy with the simulator!)
        focus_active_pos = self.focus.getMetadata()[model.MD_FAV_POS_ACTIVE]
        self.focus.moveAbsSync(focus_active_pos)

    def test_get_stream_based_bbox(self):
        # test when inside range, not whole grid
        pos = {"x": 0, "y": 0}
        streams = [self.fm_streams[0]]
        fov = get_fov(streams[0])
        rng = {"x": (-0.1, 0.1), "y": (-0.1, 0.1)}
        nx, ny = 5, 5
        overlap = 0.1
        bbox = get_stream_based_bbox(
            pos=pos,
            streams=streams,
            tiles_nx=nx,
            tiles_ny=ny,
            tiling_rng=rng,
            overlap=overlap,
        )

        w = nx * fov[0] * (1 - overlap)
        h = ny * fov[1] * (1 - overlap)
        numpy.testing.assert_array_almost_equal(bbox, [-w/2, -h/2, w/2, h/2])

        # test when outside range
        pos = {"x": 0.2, "y": 0.2}
        bbox = get_stream_based_bbox(
            pos=pos,
            streams=streams,
            tiles_nx=nx,
            tiles_ny=ny,
            tiling_rng=rng,
            overlap=overlap,
        )
        self.assertEqual(bbox, None)

    def test_get_tiled_bboxes(self):
        # test whole grid
        selected_grids = ["GRID 1", "GRID 2"]
        sample_centers_raw = self.stage_bare.getMetadata()[model.MD_SAMPLE_CENTERS]
        sample_centers = [(v["x"], v["y"]) for v in sample_centers_raw.values()]

        SAMPLE_RADIUS_TEM_GRID = 1.25e-3
        hwidth = SAMPLE_RADIUS_TEM_GRID / math.sqrt(2)
        SAMPLE_USABLE_BBOX_TEM_GRID = (-hwidth, -hwidth, hwidth, hwidth)
        rel_bbox = SAMPLE_USABLE_BBOX_TEM_GRID

        areas = get_tiled_bboxes(
            rel_bbox=rel_bbox,
            sample_centers=sample_centers,
        )

        computed_areas = []
        for center in sample_centers:

            computed_areas.append(
                (center[0] + rel_bbox[0],
                 center[1] + rel_bbox[1],
                 center[0] + rel_bbox[2],
                 center[1] + rel_bbox[3])
            )
        self.assertEqual(len(areas), len(selected_grids))
        numpy.testing.assert_array_almost_equal(areas, computed_areas)

    def test_get_fov_based_bbox(self):

        # test when inside range
        pos = {"x": 0, "y": 0}
        streams = [self.fm_streams[0]]
        fov = get_fov(streams[0])
        rng = {"x": (-0.1, 0.1), "y": (-0.1, 0.1)}
        nx, ny = 5, 5
        overlap = 0.1
        bbox = get_fov_based_bbox(
            pos=pos,
            fov=fov,
            tiles_nx=nx,
            tiles_ny=ny,
            tiling_rng=rng,
            overlap=overlap
        )

        w = nx * fov[0] * (1 - overlap)
        h = ny * fov[1] * (1 - overlap)
        numpy.testing.assert_array_almost_equal(bbox, [-w/2, -h/2, w/2, h/2])

        # test when outside range
        pos = {"x": 0.2, "y": 0.2}
        bbox = get_fov_based_bbox(
            pos=pos,
            fov=fov,
            tiles_nx=nx,
            tiles_ny=ny,
            tiling_rng=rng,
            overlap=overlap
        )
        self.assertEqual(bbox, None)

        # test various horizontal and vertical repeats
        pos = {"x": 0.0, "y": 0.0}
        fov = (0.00017, 0.00017)  # Mock fov to be uniform
        overlap = 0  # Set to zero, to make non-square comparison easier
        test_repeat_cases = [
            # Square case
            {"nx": 5, "ny": 5},
            # Non-square cases
            {"nx": 5, "ny": 3},
            {"nx": 5, "ny": 2},
            {"nx": 5, "ny": 1}
        ]

        for test_repeat_case in test_repeat_cases:
            bbox = get_fov_based_bbox(
                pos=pos,
                fov=fov,
                tiles_nx=test_repeat_case["nx"],
                tiles_ny=test_repeat_case["ny"],
                tiling_rng=rng,
                overlap=overlap
            )
            x_size = bbox[2] - bbox[0]
            y_size = bbox[3] - bbox[1]
            # If we divide the size of the resulting bounding box by the repeat count,
            # an identical size is expected
            self.assertEqual(
                x_size / test_repeat_case["nx"],
                y_size / test_repeat_case["ny"],
                test_repeat_case
            )

    def test_clip_tiling_bbox_to_range(self):
        # test area when inside range
        pos = {"x": 0, "y": 0}
        tiling_range = {"x": (-100, 100), "y": (-100, 100)}
        w, h = 50, 50
        area = clip_tiling_bbox_to_range(w=w, h=h, pos=pos, tiling_rng=tiling_range)
        numpy.testing.assert_array_almost_equal(area, [-25, -25, 25, 25])

        # test area when cliping to range
        pos = {"x": 0, "y": 0}
        area = clip_tiling_bbox_to_range(w=500, h=500, pos=pos, tiling_rng=tiling_range)
        numpy.testing.assert_array_almost_equal(area, [-100, -100, 100, 100])

        # test when outside of range
        pos = {"x": 500, "y": 500}
        area = clip_tiling_bbox_to_range(w=100, h=100, pos=pos, tiling_rng=tiling_range)
        self.assertEqual(area, None)

    def test_get_zstack_levels(self):
        focuser = self.focus

        # return None when 1 zstep is provided
        zlevels = get_zstack_levels(zsteps=1, zstep_size=None, focuser=focuser)
        self.assertEqual(zlevels, None)

        # assert raises error without focuser
        with self.assertRaises(ValueError):
            zlevels = get_zstack_levels(zsteps=3, zstep_size=10e-6, focuser=None)

        # relative z levels
        zlevels = get_zstack_levels(zsteps=3, zstep_size=10e-6, rel=True)
        numpy.testing.assert_array_almost_equal(zlevels, [-15e-6, 0, 15e-6])

        # absolute z levels
        zlevels = get_zstack_levels(zsteps=3, zstep_size=10e-6, rel=False, focuser=focuser)
        foc_z = focuser.position.value['z']
        numpy.testing.assert_array_almost_equal(zlevels, [foc_z-15e-6, foc_z, foc_z+15e-6])

        # shift z levels when outside focus range
        frange = focuser.axes['z'].range
        max_range = frange[1] - frange[0]
        zlevels = get_zstack_levels(zsteps=10, zstep_size=max_range / 5, rel=False, focuser=focuser)
        self.assertAlmostEqual(zlevels[0], frange[0])
        self.assertAlmostEqual(zlevels[-1], frange[1])
        self.assertEqual(len(zlevels), 10)

        # shift z levels when outside focus range
        focuser.moveAbs({"z": frange[0]}).result() # move to the minimum position
        zlevels = get_zstack_levels(zsteps=10, zstep_size=2e-6, rel=False, focuser=focuser)
        self.assertAlmostEqual(zlevels[0], frange[0]) # minimum z level should be at minimum position

if __name__ == '__main__':
    unittest.main()
