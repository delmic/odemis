# -*- coding: utf-8 -*-
"""
Created on 15th July 2021

@author: Sabrina Rossberger, Thera Pals, Éric Piel

Copyright © 2021-2022 Sabrina Rossberger, Delmic

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
import json
import logging
import math
import os
import time
import unittest
from concurrent.futures._base import CancelledError
from unittest.mock import Mock

import numpy
from shapely.geometry import Polygon

import odemis
from odemis import model
from odemis.acq import fastem, stream
from odemis.acq.acqmng import SettingsObserver
from odemis.acq.align.fastem import Calibrations
from odemis.acq.fastem import DEFAULT_PITCH, SETTINGS_SELECTION
from odemis.gui.comp.fastem_roa import FastEMROA
from odemis.gui.comp.overlay.shapes import EditableShape
from odemis.gui.model.main_gui_data import FastEMMainGUIData
from odemis.util import driver, get_polygon_bbox, img, is_point_in_rect, testing

# * TEST_NOHW = 1: connected to the simulator or not connected to anything
# * TEST_NOHW = 0: connected to the real hardware, the backend should be running
# technolution_asm_simulator/simulator2/run_the_simulator.sh
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default is hardware testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
FASTEM_CONFIG = CONFIG_PATH + "sim/fastem-sim.odm.yaml"
FASTEM_CONFIG_ASM = CONFIG_PATH + "sim/fastem-sim-asm.odm.yaml"


class MockEditableShape(EditableShape):
    def __init__(self, cnvs=None):
        super().__init__(cnvs)

    def check_point_proximity(self, v_point):
        return False

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        pass

    def copy(self):
        return MockEditableShape()

    def move_to(self, pos):
        pass

    def get_state(self):
        return {}

    def restore_state(self, state):
        pass

    def to_dict(self):
        return {}

    @staticmethod
    def from_dict(shape, tab_data):
        return MockEditableShape()

    def set_rotation(self, target_rotation):
        pass

    def reset(self):
        pass


class TestFASTEMOverviewAcquisition(unittest.TestCase):
    """Test FASTEM overview image acquisition."""

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            testing.start_backend(FASTEM_CONFIG)
        elif driver.get_backend_status() != driver.BACKEND_RUNNING:
            raise IOError("Backend controlling a real hardware should be started before running this test case")

        cls.ebeam = model.getComponent(role="e-beam")
        cls.efocuser = model.getComponent(role="ebeam-focus")
        cls.sed = model.getComponent(role="se-detector")
        cls.stage = model.getComponent(role="stage")
        cls.stage.reference({"x", "y"}).result()

    def setUp(self):
        self.stream = stream.SEMStream("Single beam", self.sed, self.sed.data, self.ebeam,
                                       focuser=self.efocuser,  # Not used during acquisition, but done by the GUI
                                       hwemtvas={"scale", "dwellTime", "horizontalFoV"})
        self.acquisition_cancelled = False

    def test_overview_acquisition(self):
        """Test the full overview image acquisition."""
        # This should be used by the acquisition
        self.stream.dwellTime.value = 1e-6  # s

        # Use random settings and check they are overridden by the overview acquisition
        self.stream.scale.value = (2, 2)
        self.stream.horizontalFoV.value = 20e-6  # m

        # Known position of the center scintillator in the sample carrier coordinate system
        scintillator5_area = (-0.007, -0.007, 0.007, 0.007)  # l, b, r, t
        # Small area for DEBUG (3x3)
        # scintillator5_area = (-0.002, -0.002, 0.002, 0.002)  # l, b, r, t

        est_time = fastem.estimateTiledAcquisitionTime(self.stream, self.stage, scintillator5_area)
        # don't use for DEBUG example
        self.assertGreater(est_time, 10)  # It should take more than 10s! (expect ~5 min)

        before_start_t = time.time()
        f = fastem.acquireTiledArea(self.stream, self.stage, scintillator5_area)
        time.sleep(1)
        start_t, end_t = f.get_progress()
        self.assertGreater(start_t, before_start_t)
        # don't use for DEBUG example
        self.assertGreater(end_t, time.time() + 10)  # Should report still more than 10s

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

    def test_estimateTiledAcquisitionTime(self):
        """Test estimated acquisition time for overview imaging."""

        # small area on center scintillator (~3x4 tiles)
        scintillator5_area = (-0.002, -0.002, 0.002, 0.002)  # l, b, r, t

        self.stream.emitter.dwellTime.value = 1e-6  # s
        est_time_1 = fastem.estimateTiledAcquisitionTime(self.stream, self.stage, scintillator5_area)

        # increase dwell time
        self.stream.emitter.dwellTime.value = 2e-6  # s
        est_time_2 = fastem.estimateTiledAcquisitionTime(self.stream, self.stage, scintillator5_area)

        # check that estimated time increases with dwell time
        self.assertGreater(est_time_2, est_time_1)

    def test_overview_acquisition_cancel(self):
        """Test cancelling the overview image acquisition."""
        # This should be used by the acquisition
        self.stream.dwellTime.value = 1e-6  # s

        # Known position of the center scintillator in the sample carrier coordinate system
        scintillator5_area = (-0.007, -0.007, 0.007, 0.007)  # l, b, r, t

        # Future to acquire the overview image
        f = fastem.acquireTiledArea(self.stream, self.stage, scintillator5_area)
        f.add_done_callback(self.on_acquisition_done)

        # Wait a bit for the acquisition to happen and then cancel it
        time.sleep(5)
        f.cancel()
        time.sleep(1)
        # Assert that the acquisition was cancelled
        self.assertTrue(self.acquisition_cancelled)

    def on_acquisition_done(self, future):
        """Callback called when the one overview image acquisition is finished."""
        try:
            future.result()
        except CancelledError:
            self.acquisition_cancelled = True
            return
        except Exception:
            return


class TestFastEMROA(unittest.TestCase):
    """Test region of acquisition (ROA) class methods."""

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            testing.start_backend(FASTEM_CONFIG_ASM)
        elif driver.get_backend_status() != driver.BACKEND_RUNNING:
            raise IOError("Backend controlling a real hardware should be started before running this test case")

        # get the hardware components
        cls.asm = model.getComponent(role="asm")
        cls.mppc = model.getComponent(role="mppc")
        cls.multibeam = model.getComponent(role="multibeam")
        cls.descanner = model.getComponent(role="descanner")

        # Create the FastEMMainGUIData
        cls.microscope = model.getMicroscope()
        cls.main_data = FastEMMainGUIData(microscope=cls.microscope)
        cls.main_data.asm = cls.asm
        cls.main_data.multibeam = cls.multibeam
        cls.main_data.descanner = cls.descanner
        cls.main_data.mppc = cls.mppc

    def test_get_poly_field_indices(self):
        """Test that the correct indices are returned and that they are in the right order."""
        x_fields = 5
        y_fields = 4
        self.multibeam.resolution.value = (6400, 6400)  # don't change
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        xmax = res_x * px_size_x * x_fields
        ymax = res_y * px_size_y * y_fields
        coordinates = (0, 0, xmax, ymax)  # in m, don't change
        polygon = [(0, 0), (0, ymax - px_size_y), (xmax - px_size_x, 0)]
        roc_2 = fastem.FastEMROC("roc_2", 0, coordinates)
        roc_3 = fastem.FastEMROC("roc_3", 0, coordinates)
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=0.0,
                        name=roa_name,
                        slice_index=0)
        roa.roc_2.value = roc_2
        roa.roc_3.value = roc_3
        roa.polygon_shape = Polygon(polygon)

        expected_indices = [(0, 0), (1, 0),
                            (0, 1), (1, 1), (2, 1),
                            (0, 2), (1, 2), (2, 2), (3, 2),
                            (0, 3), (1, 3), (2, 3), (3, 3), (4, 3)]  # (col, row)

        roa.calculate_field_indices()
        self.assertListEqual(expected_indices, roa.field_indices)

    def test_get_poly_field_indices_overlap(self):
        """Test that the correct indices are returned and are in the right order with different sizes of overlap."""
        x_fields = 5
        y_fields = 4
        self.multibeam.resolution.value = (6400, 6400)  # don't change
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value

        roa_name = "test_megafield_id"

        field_size_x = res_x * px_size_x
        field_size_y = res_y * px_size_y

        expected_indices = [(0, 0), (1, 0),
                            (0, 1), (1, 1), (2, 1),
                            (0, 2), (1, 2), (2, 2), (3, 2),
                            (0, 3), (1, 3), (2, 3), (3, 3), (4, 3)]  # (col, row)

        for overlap in (0, 0.0625, 0.2, 0.5, 0.7):
            xmin, ymin = (0, 0)
            xmax, ymax = (field_size_x * x_fields * (1 - overlap) + field_size_x * overlap,
                          field_size_y * y_fields * (1 - overlap) + field_size_y * overlap)
            polygon = [(xmin, ymin), (xmin, ymax - px_size_y), (xmax - px_size_x, ymin)]
            roa = FastEMROA(shape=MockEditableShape(),
                            main_data=self.main_data,
                            overlap=overlap,
                            name=roa_name,
                            slice_index=0)
            roa.polygon_shape = Polygon(polygon)

            roa.calculate_field_indices()

            max_extra_fields = int(1 / (1 - overlap))

            exp_x_min, exp_y_min, exp_x_max, exp_y_max = get_polygon_bbox(expected_indices)
            res_x_min, res_y_min, res_x_max, res_y_max = get_polygon_bbox(roa.field_indices)
            self.assertEqual(exp_x_min, res_x_min)
            self.assertEqual(exp_y_min, res_y_min)
            self.assertLessEqual(res_x_max - exp_x_max, max_extra_fields)
            self.assertLessEqual(res_y_max - exp_y_max, max_extra_fields)
            self.assertGreaterEqual(res_x_max - exp_x_max, 0)
            self.assertGreaterEqual(res_y_max - exp_y_max, 0)

    def test_get_poly_field_indices_overlap_small_roa(self):
        """Check that the correct number of field indices are calculated for ROA's that are smaller than the overlap."""
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        overlap = 0.2

        roa_name = "test_megafield_id"

        field_size_x = res_x * px_size_x
        field_size_y = res_y * px_size_y
        # The coordinates of the ROA in meters.
        xmin, ymin = (0, 0)
        # Create xmax and ymax such that they are smaller than the field_size * overlap.
        xmax, ymax = (0.8 * field_size_x * overlap,
                      0.8 * field_size_y * overlap)
        polygon = [(0, 0), (xmax - px_size_x, ymax - px_size_y), (0, ymax - px_size_y)]
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=overlap,
                        name=roa_name,
                        slice_index=0)
        roa.polygon_shape = Polygon(polygon)

        # For very a small ROA only single field is expected.
        expected_indices = [(0, 0)]  # (col, row)
        roa.calculate_field_indices()
        self.assertListEqual(roa.field_indices, expected_indices)


class TestFastEMAcquisition(unittest.TestCase):
    """Test multibeam acquisition."""

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            testing.start_backend(FASTEM_CONFIG_ASM)
        elif driver.get_backend_status() != driver.BACKEND_RUNNING:
            raise IOError("Backend controlling a real hardware should be started before running this test case")

        # get the hardware components
        cls.scanner = model.getComponent(role='e-beam')
        cls.asm = model.getComponent(role="asm")
        cls.mppc = model.getComponent(role="mppc")
        cls.multibeam = model.getComponent(role="multibeam")
        cls.descanner = model.getComponent(role="descanner")
        cls.stage = model.getComponent(role="stage")
        cls.scan_stage = model.getComponent(role="scan-stage")
        cls.ccd = model.getComponent(role="diagnostic-ccd")
        cls.beamshift = model.getComponent(role="ebeam-shift")
        cls.lens = model.getComponent(role="lens")
        cls.se_detector = model.getComponent(role="se-detector")
        cls.ebeam_focus = model.getComponent(role="ebeam-focus")

        # Normally the beamshift MD_CALIB is set when running the calibrations.
        # Set it here explicitly because we do not run the calibrations in these test cases.
        cls.beamshift.updateMetadata({model.MD_CALIB: cls.scanner.beamShiftTransformationMatrix.value})
        cls.beamshift.shift.value = (0, 0)
        cls.stage.reference({"x", "y"}).result()
        cls.init_rot_cor = cls.scan_stage.getMetadata()[model.MD_ROTATION_COR]
        cls.scan_stage.updateMetadata({model.MD_ROTATION_COR: 0.0})

        # Create the FastEMMainGUIData
        cls.microscope = model.getMicroscope()
        cls.main_data = FastEMMainGUIData(microscope=cls.microscope)
        cls.main_data.asm = cls.asm
        cls.main_data.multibeam = cls.multibeam
        cls.main_data.descanner = cls.descanner
        cls.main_data.mppc = cls.mppc

    @classmethod
    def tearDownClass(cls):
        cls.scan_stage.updateMetadata({model.MD_ROTATION_COR: cls.init_rot_cor})

    def test_acquire_ROA(self):
        """Acquire a small mega field image with ROA matching integer multiple of single field size."""
        x_fields = 2
        y_fields = 3
        overlap = 0.06
        self.multibeam.resolution.value = (6400, 6400)  # don't change
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        # Note: Do not change those values; _calculate_field_indices handles floating point errors the same way
        # as an ROA that does not match an integer number of field indices by just adding an additional row or column
        # of field images.
        top = -0.002  # top corner coordinate of ROA in stage coordinates in meter
        left = +0.001  # left corner coordinate of ROA in stage coordinates in meter
        coordinates = (top, left,
                       top + res_x * px_size_x * x_fields * (1 - overlap),
                       left + res_y * px_size_y * y_fields * (1 - overlap))  # in m
        points = [
            (coordinates[0], coordinates[1]),  # xmin, ymin
            (coordinates[2], coordinates[1]),  # xmax, ymin
            (coordinates[0], coordinates[3]),  # xmin, ymax
            (coordinates[2], coordinates[3]),  # xmax, ymax
        ]
        roc_2 = fastem.FastEMROC("roc_2", 0, coordinates)
        roc_3 = fastem.FastEMROC("roc_3", 0, coordinates)
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=overlap,
                        name=roa_name,
                        slice_index=0)
        roa.roc_2.value = roc_2
        roa.roc_3.value = roc_3
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        path_storage = "test_project_megafield"
        f = fastem.acquire(roa, path_storage, "default",
                           self.scanner, self.multibeam, self.descanner, self.mppc, self.stage, self.scan_stage,
                           self.ccd, self.beamshift, self.lens, self.se_detector, self.ebeam_focus)
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
        overlap = 0.06
        self.multibeam.resolution.value = (6400, 6400)  # don't change
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        # some extra pixels (< 1 field) to be added to the ROA
        x_margin, y_margin = (res_x / 10, res_y / 20)
        coordinates = (0, 0,
                       res_x * px_size_x * x_fields + x_margin * px_size_x * (1 - overlap),
                       res_y * px_size_y * y_fields + y_margin * px_size_y * (1 - overlap))  # in m
        points = [
            (coordinates[0], coordinates[1]),  # xmin, ymin
            (coordinates[2], coordinates[1]),  # xmax, ymin
            (coordinates[0], coordinates[3]),  # xmin, ymax
            (coordinates[2], coordinates[3]),  # xmax, ymax
        ]
        roc_2 = fastem.FastEMROC("roc_2", 0, coordinates)
        roc_3 = fastem.FastEMROC("roc_3", 0, coordinates)
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=overlap,
                        name=roa_name,
                        slice_index=0)
        roa.roc_2.value = roc_2
        roa.roc_3.value = roc_3
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        path_storage = "test_project_field_indices"
        f = fastem.acquire(roa, path_storage, "default",
                           self.scanner, self.multibeam, self.descanner, self.mppc,
                           self.stage, self.scan_stage, self.ccd, self.beamshift, self.lens,
                           self.se_detector, self.ebeam_focus)
        data, e = f.result()

        self.assertIsNone(e)  # check no exceptions were returned
        # check data returned contains the correct number of field images
        # expect plus 1 field in x and y respectively
        self.assertGreater(len(data), x_fields * y_fields)
        self.assertIsInstance(data[(0, 0)], model.DataArray)

    def test_progress_ROA(self):
        """Check if some progress is reported between the field images acquired for the ROA (megafield)."""
        x_fields = 2
        y_fields = 3
        overlap = 0.06
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        coordinates = (0,
                       0,
                       res_x * px_size_x * x_fields * (1 - overlap),
                       res_y * px_size_y * y_fields * (1 - overlap))  # in m
        points = [
            (coordinates[0], coordinates[1]),  # xmin, ymin
            (coordinates[2], coordinates[1]),  # xmax, ymin
            (coordinates[0], coordinates[3]),  # xmin, ymax
            (coordinates[2], coordinates[3]),  # xmax, ymax
        ]
        roc_2 = fastem.FastEMROC("roc_2", 0, coordinates)
        roc_3 = fastem.FastEMROC("roc_3", 0, coordinates)
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=overlap,
                        name=roa_name,
                        slice_index=0)
        roa.roc_2.value = roc_2
        roa.roc_3.value = roc_3
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        path_storage = "test_project_progress"
        f = fastem.acquire(roa, path_storage, "default",
                           self.scanner, self.multibeam, self.descanner, self.mppc,
                           self.stage, self.scan_stage, self.ccd, self.beamshift, self.lens,
                           self.se_detector, self.ebeam_focus)
        f.add_done_callback(self.on_done)  # callback executed when f.set_result is called (via bindFuture)

        data, e = f.result()

        self.assertIsNone(e)  # check no exceptions were returned
        self.assertIsInstance(data[(0, 0)], model.DataArray)
        self.assertTrue(self.done)

    def test_cancel_ROA(self):
        """Test if it is possible to cancel between field images acquired for one ROA."""
        x_fields = 2
        y_fields = 3
        overlap = 0.06
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        coordinates = (0,
                       0,
                       res_x * px_size_x * x_fields * (1 - overlap),
                       res_y * px_size_y * y_fields * (1 - overlap))  # in m
        points = [
            (coordinates[0], coordinates[1]),  # xmin, ymin
            (coordinates[2], coordinates[1]),  # xmax, ymin
            (coordinates[0], coordinates[3]),  # xmin, ymax
            (coordinates[2], coordinates[3]),  # xmax, ymax
        ]
        roc_2 = fastem.FastEMROC("roc_2", 0, coordinates)
        roc_3 = fastem.FastEMROC("roc_3", 0, coordinates)
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=overlap,
                        name=roa_name,
                        slice_index=0)
        roa.roc_2.value = roc_2
        roa.roc_3.value = roc_3
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        self.done = False  # updated in callback on_done

        path_storage = "test_project_cancel"
        f = fastem.acquire(roa, path_storage, "default",
                           self.scanner, self.multibeam, self.descanner, self.mppc,
                           self.stage, self.scan_stage, self.ccd, self.beamshift, self.lens,
                           self.se_detector, self.ebeam_focus)
        f.add_done_callback(self.on_done)  # callback executed when f.set_result is called (via bindFuture)

        time.sleep(1)  # make sure it's started
        self.assertTrue(f.running())
        f.cancel()

        with self.assertRaises(CancelledError):
            f.result(timeout=5)  # add timeout = 5s in case cancellation error was not raised
        self.assertTrue(self.done)
        self.assertTrue(f.cancelled())

    def test_stage_movement(self):
        """Test that the stage move corresponds to one field image (excluding over-scanned pixels)."""
        x_fields = 2
        y_fields = 3
        overlap = 0.06
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value

        # Note: Do not change those values; _calculate_field_indices handles floating point errors the same way
        # as an ROA that does not match an integer number of field indices by just adding an additional row or column
        # of field images.
        xmin = -0.002  # top corner coordinate of ROA in stage coordinates in meter
        ymin = +0.001  # left corner coordinate of ROA in stage coordinates in meter
        xmax = xmin + res_x * px_size_x * x_fields * (1 - overlap)
        ymax = ymin + res_y * px_size_y * y_fields * (1 - overlap)
        coordinates = (xmin, ymin, xmax, ymax)  # in m
        points = [
            (coordinates[0], coordinates[1]),  # xmin, ymin
            (coordinates[2], coordinates[1]),  # xmax, ymin
            (coordinates[0], coordinates[3]),  # xmin, ymax
            (coordinates[2], coordinates[3]),  # xmax, ymax
        ]
        roc_2 = fastem.FastEMROC("roc_2", 0, coordinates)
        roc_3 = fastem.FastEMROC("roc_3", 0, coordinates)
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=overlap,
                        name=roa_name,
                        slice_index=0)
        roa.roc_2.value = roc_2
        roa.roc_3.value = roc_3
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        path_storage = "test_project_stage_move"
        f = fastem.acquire(roa, path_storage, "default",
                           self.scanner, self.multibeam, self.descanner, self.mppc,
                           self.stage, self.scan_stage, self.ccd, self.beamshift, self.lens,
                           self.se_detector, self.ebeam_focus)
        data, e = f.result()

        self.assertIsNone(e)  # check no exceptions were returned

        # total expected stage movement in x and y during the acquisition
        # half a field to start at center of first field image
        exp_move_x = res_x / 2. * px_size_x + res_x * px_size_x * (x_fields - 1) * (1 - overlap)
        exp_move_y = res_y / 2. * px_size_y + res_y * px_size_y * (y_fields - 1) * (1 - overlap)

        # In role="stage" coordinate system:
        # Move in the positive x direction, because the second field should be right of the first.
        # Move in the negative y direction, because the second field should be below the first.
        exp_position = (xmin + exp_move_x, ymax - exp_move_y)
        # get the last stage position (it is the center of the last field)
        cur_position = (self.stage.position.value['x'], self.stage.position.value['y'])

        # check stage position is matching expected position (Note: stage accuracy is ~ TODO fix decimal accordingly)
        numpy.testing.assert_almost_equal(exp_position, cur_position, decimal=6)

    def test_stage_movement_rotation_correction(self):
        """
        Test that the stage move corresponds to one field image
        (excluding over-scanned pixels) with rotation correction.
        """
        x_fields = 1
        y_fields = 1
        overlap = 0.06
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value

        rot_cor = math.radians(45)
        self.scan_stage.updateMetadata({model.MD_ROTATION_COR: rot_cor})
        # Note: Do not change those values; _calculate_field_indices handles floating point errors the same way
        # as an ROA that does not match an integer number of field indices by just adding an additional row or column
        # of field images.
        xmin = 0  # -0.002  # top corner coordinate of ROA in stage coordinates in meter
        ymin = 0  # +0.001  # left corner coordinate of ROA in stage coordinates in meter
        xmax = xmin + res_x * px_size_x * x_fields * (1 - overlap)
        ymax = ymin + res_y * px_size_y * y_fields * (1 - overlap)
        coordinates = (xmin, ymin, xmax, ymax)  # in m
        points = [
            (coordinates[0], coordinates[1]),  # xmin, ymin
            (coordinates[2], coordinates[1]),  # xmax, ymin
            (coordinates[0], coordinates[3]),  # xmin, ymax
            (coordinates[2], coordinates[3]),  # xmax, ymax
        ]
        roc_2 = fastem.FastEMROC("roc_2", 0, coordinates)
        roc_3 = fastem.FastEMROC("roc_3", 0, coordinates)
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=overlap,
                        name=roa_name,
                        slice_index=0)
        roa.roc_2.value = roc_2
        roa.roc_3.value = roc_3
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        path_storage = "test_project_stage_move_rot_cor"
        f = fastem.acquire(roa, path_storage, "default",
                           self.scanner, self.multibeam, self.descanner, self.mppc,
                           self.stage, self.scan_stage, self.ccd, self.beamshift, self.lens,
                           self.se_detector, self.ebeam_focus)
        data, e = f.result()

        self.assertIsNone(e)  # check no exceptions were returned

        # get the last stage position (it is the center of the last field)
        cur_position = (self.stage.position.value['x'], self.stage.position.value['y'])
        # For a single field and a positive 45 degree rotation correction it is expected that the stage moves only in x.
        # It should move by half the diagonal size of a field in x.
        exp_pos_x = exp_pos_x = math.hypot(res_x / 2 * px_size_x, res_y / 2 * px_size_y)
        exp_pos_y = ymax
        # check stage position is matching expected position (Note: stage accuracy is ~ TODO fix decimal accordingly)
        numpy.testing.assert_almost_equal((exp_pos_x, exp_pos_y), cur_position, decimal=7)
        self.scan_stage.updateMetadata({model.MD_ROTATION_COR: rot_cor})

    def on_done(self, future):
        self.done = True


class TestFastEMAcquisitionTask(unittest.TestCase):
    """Test methods of the fastem.AcquisitionTask class."""

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            testing.start_backend(FASTEM_CONFIG_ASM)
        elif driver.get_backend_status() != driver.BACKEND_RUNNING:
            raise IOError("Backend controlling a real hardware should be started before running this test case")

        # Get the hardware components from the simulators or hardware
        cls.scanner = model.getComponent(role='e-beam')
        cls.asm = model.getComponent(role="asm")
        cls.mppc = model.getComponent(role="mppc")
        cls.multibeam = model.getComponent(role="multibeam")
        cls.descanner = model.getComponent(role="descanner")
        cls.stage = model.getComponent(role="stage")
        cls.scan_stage = model.getComponent(role="scan-stage")
        cls.ccd = model.getComponent(role="diagnostic-ccd")
        cls.beamshift = model.getComponent(role="ebeam-shift")
        cls.lens = model.getComponent(role="lens")
        cls.se_detector = model.getComponent(role="se-detector")
        cls.ebeam_focus = model.getComponent(role="ebeam-focus")

        cls.beamshift.shift.value = (0, 0)
        cls.stage.reference({"x", "y"}).result()
        cls.init_rot_cor = cls.scan_stage.getMetadata()[model.MD_ROTATION_COR]
        cls.scan_stage.updateMetadata({model.MD_ROTATION_COR: 0.0})

        # Create the FastEMMainGUIData
        cls.microscope = model.getMicroscope()
        cls.main_data = FastEMMainGUIData(microscope=cls.microscope)
        cls.main_data.asm = cls.asm
        cls.main_data.multibeam = cls.multibeam
        cls.main_data.descanner = cls.descanner
        cls.main_data.mppc = cls.mppc

    @classmethod
    def tearDownClass(cls):
        cls.scan_stage.updateMetadata({model.MD_ROTATION_COR: cls.init_rot_cor})

    def test_get_abs_stage_movement(self):
        """
        Test the correct stage positions are returned for the corner fields of
        ROAs consisting of a varying number of single fields.
        """
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value

        # Loop over different ROA sizes by varying the number of fields in x and y.
        for x_fields, y_fields in zip((1, 2, 40, 34, 5), (1, 22, 43, 104, 25)):
            # The coordinates of the ROA in meters.
            xmin, ymin, xmax, ymax = (0, 0, res_x * px_size_x * x_fields, res_y * px_size_y * y_fields)
            coordinates = (xmin, ymin, xmax, ymax)  # in m
            points = [
                (coordinates[0], coordinates[1]),  # xmin, ymin
                (coordinates[2], coordinates[1]),  # xmax, ymin
                (coordinates[0], coordinates[3]),  # xmin, ymax
                (coordinates[2], coordinates[3]),  # xmax, ymax
            ]

            # Create an ROA with the coordinates of the field.
            roa_name = "test_megafield_id"
            roa = FastEMROA(shape=MockEditableShape(),
                            main_data=self.main_data,
                            overlap=0.0,
                            name=roa_name,
                            slice_index=0)
            roa.shape._points = points
            roa.shape.points.value = points

            # Give sometime for calculation of field_indices
            time.sleep(1)

            task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                          self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                          self.ebeam_focus, roa, path=None, username="default", pre_calibrations=None,
                                          save_full_cells=False, settings_obs=None, spot_grid_thresh=0.5,
                                          blank_beam=True, stop_acq_on_failure=True, future=None)

            # Set the _pos_first_tile, which would normally be set in the run function.
            task._pos_first_tile = task.get_pos_first_tile()

            # Verify that compared to the top left corner of the ROA, the stage
            # position is located half a field to the bottom right.
            task.field_idx = (0, 0)  # (0, 0) is the index of the first field

            # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
            expected_position = (xmin + res_x / 2 * px_size_x,
                                 ymax - res_x / 2 * px_size_y)  # [m]
            actual_position = task.get_abs_stage_movement()  # [m]
            actual_position_first_tile = task.get_pos_first_tile()  # [m]
            numpy.testing.assert_allclose(actual_position, expected_position)
            numpy.testing.assert_allclose(actual_position, actual_position_first_tile)

            # Verify that compared to the bottom right corner of the ROA, the stage
            # position is located half a field to the top left.
            task.field_idx = (x_fields - 1, y_fields - 1)  # index of the last field

            # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
            expected_position = (xmax - res_x / 2 * px_size_x,
                                 ymin + res_x / 2 * px_size_y)  # [m]
            actual_position = task.get_abs_stage_movement()  # [m]
            numpy.testing.assert_allclose(actual_position, expected_position)

            # Verify that compared to the top right corner of the ROA, the stage
            # position is located half a field to the bottom left.
            task.field_idx = (x_fields - 1, 0)  # index of the last field in x and first field in y
            expected_position = (xmax - res_x / 2 * px_size_x,
                                 ymax - res_x / 2 * px_size_y)  # [m]
            actual_position = task.get_abs_stage_movement()  # [m]
            numpy.testing.assert_allclose(actual_position, expected_position)

            # Verify that compared to the bottom left corner of the ROA, the stage
            # position is located half a field to the top right.
            task.field_idx = (0, y_fields - 1)  # index of the first field in x and the last field in y

            # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
            expected_position = (xmin + res_x / 2 * px_size_x,
                                 ymin + res_x / 2 * px_size_y)  # [m]
            actual_position = task.get_abs_stage_movement()  # [m]
            numpy.testing.assert_allclose(actual_position, expected_position)

    def test_get_abs_stage_movement_overlap(self):
        """
        Test the correct stage positions are returned for the corner fields of
        ROAs when there is an overlap in between fields.
        """
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        x_fields, y_fields = (3, 4)
        field_size_x = res_x * px_size_x
        field_size_y = res_y * px_size_y

        for overlap in (0, 0.0625, 0.2, 0.5, 0.7):
            # The coordinates of the ROA in meters.
            xmin, ymin = (0, 0)
            # The max field size is the number of fields multiplied with the field size including overlap, plus
            # the extra overlap added to the end.
            xmax, ymax = (field_size_x * x_fields * (1 - overlap) + field_size_x * overlap,
                          field_size_y * y_fields * (1 - overlap) + field_size_y * overlap)
            coordinates = (xmin, ymin, xmax, ymax)  # in m
            points = [
                (coordinates[0], coordinates[1]),  # xmin, ymin
                (coordinates[2], coordinates[1]),  # xmax, ymin
                (coordinates[0], coordinates[3]),  # xmin, ymax
                (coordinates[2], coordinates[3]),  # xmax, ymax
            ]

            # Create an ROA with the coordinates of the field.
            roa_name = "test_megafield_id"
            roa = FastEMROA(shape=MockEditableShape(),
                            main_data=self.main_data,
                            overlap=overlap,
                            name=roa_name,
                            slice_index=0)
            roa.shape._points = points
            roa.shape.points.value = points

            # Give sometime for calculation of field_indices
            time.sleep(1)

            task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                          self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                          self.ebeam_focus, roa, path=None, username="default", pre_calibrations=None,
                                          save_full_cells=False, settings_obs=None, spot_grid_thresh=0.5,
                                          blank_beam=True, stop_acq_on_failure=True, future=None)

            # Set the _pos_first_tile, which would normally be set in the run function.
            task._pos_first_tile = task.get_pos_first_tile()

            # Verify that compared to the top left corner of the ROA, the stage
            # position is located half a field to the bottom right.
            task.field_idx = (0, 0)  # (0, 0) is the index of the first field

            # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
            expected_position = (xmin + res_x / 2 * px_size_x,
                                 ymax - res_x / 2 * px_size_y)  # [m]
            actual_position = task.get_abs_stage_movement()  # [m]
            numpy.testing.assert_allclose(actual_position, expected_position)

            # Verify that compared to the bottom right corner of the ROA, the stage
            # position is located half a field to the top left.
            task.field_idx = (x_fields - 1, y_fields - 1)  # index of the last field

            # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
            expected_position = (xmax - res_x / 2 * px_size_x,
                                 ymin + res_x / 2 * px_size_y)  # [m]
            actual_position = task.get_abs_stage_movement()  # [m]
            numpy.testing.assert_allclose(actual_position, expected_position)

            # Verify that compared to the top right corner of the ROA, the stage
            # position is located half a field to the bottom left.
            task.field_idx = (x_fields - 1, 0)  # index of the last field in x and first field in y
            expected_position = (xmax - res_x / 2 * px_size_x,
                                 ymax - res_x / 2 * px_size_y)  # [m]
            actual_position = task.get_abs_stage_movement()  # [m]
            numpy.testing.assert_allclose(actual_position, expected_position)

            # Verify that compared to the bottom left corner of the ROA, the stage
            # position is located half a field to the top right.
            task.field_idx = (0, y_fields - 1)  # index of the first field in x and the last field in y

            # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
            expected_position = (xmin + res_x / 2 * px_size_x,
                                 ymin + res_x / 2 * px_size_y)  # [m]
            actual_position = task.get_abs_stage_movement()  # [m]
            numpy.testing.assert_allclose(actual_position, expected_position)

    def test_get_abs_stage_movement_full_cells(self):
        """
        Test the correct stage positions are returned for the corner fields of
        ROAs when save_full_cells is enabled.
        """
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        x_fields, y_fields = (3, 4)
        field_size_x = res_x * px_size_x
        field_size_y = res_y * px_size_y
        overlap = 0.0625
        # The coordinates of the ROA in meters.
        xmin, ymin = (0, 0)
        # The max field size is the number of fields multiplied with the field size including overlap, plus
        # the extra overlap added to the end.
        xmax, ymax = (field_size_x * x_fields * (1 - overlap) + field_size_x * overlap,
                      field_size_y * y_fields * (1 - overlap) + field_size_y * overlap)
        coordinates = (xmin, ymin, xmax, ymax)  # in m
        points = [
            (coordinates[0], coordinates[1]),  # xmin, ymin
            (coordinates[2], coordinates[1]),  # xmax, ymin
            (coordinates[0], coordinates[3]),  # xmin, ymax
            (coordinates[2], coordinates[3]),  # xmax, ymax
        ]

        # Create an ROA with the coordinates of the field.
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=overlap,
                        name=roa_name,
                        slice_index=0)
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                      self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                      self.ebeam_focus, roa, path=None, username="default", pre_calibrations=None,
                                      save_full_cells=True, settings_obs=None, spot_grid_thresh=0.5, blank_beam=True,
                                      stop_acq_on_failure=True, future=None)

        # Set the _pos_first_tile, which would normally be set in the run function.
        task._pos_first_tile = task.get_pos_first_tile()

        # Verify that compared to the top left corner of the ROA, the stage
        # position is located half a field to the bottom right.
        task.field_idx = (0, 0)  # (0, 0) is the index of the first field

        # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
        expected_position = (xmin + res_x / 2 * px_size_x,
                             ymax - res_x / 2 * px_size_y)  # [m]
        actual_position = task.get_abs_stage_movement()  # [m]
        numpy.testing.assert_allclose(actual_position, expected_position)

        # Verify that compared to the bottom right corner of the ROA, the stage
        # position is located half a field to the top left.
        task.field_idx = (x_fields - 1, y_fields - 1)  # index of the last field

        # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
        expected_position = (xmax - res_x / 2 * px_size_x,
                             ymin + res_x / 2 * px_size_y)  # [m]
        actual_position = task.get_abs_stage_movement()  # [m]
        numpy.testing.assert_allclose(actual_position, expected_position)

        # Verify that compared to the top right corner of the ROA, the stage
        # position is located half a field to the bottom left.
        task.field_idx = (x_fields - 1, 0)  # index of the last field in x and first field in y
        expected_position = (xmax - res_x / 2 * px_size_x,
                             ymax - res_x / 2 * px_size_y)  # [m]
        actual_position = task.get_abs_stage_movement()  # [m]
        numpy.testing.assert_allclose(actual_position, expected_position)

        # Verify that compared to the bottom left corner of the ROA, the stage
        # position is located half a field to the top right.
        task.field_idx = (0, y_fields - 1)  # index of the first field in x and the last field in y

        # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
        expected_position = (xmin + res_x / 2 * px_size_x,
                             ymin + res_x / 2 * px_size_y)  # [m]
        actual_position = task.get_abs_stage_movement()  # [m]
        numpy.testing.assert_allclose(actual_position, expected_position)

    def test_stage_pos_outside_roa(self):
        """
        The pre-calibrations need to run outside the ROA. Test that get_abs_stage_movement returns the correct value
        for a field index of (-1, -1) and that that value is outside the ROA.
        """
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value
        x_fields, y_fields = (3, 4)
        field_size_x = res_x * px_size_x
        field_size_y = res_y * px_size_y
        # The coordinates of the ROA in meters.
        xmin, ymin = (0, 0)
        xmax, ymax = (field_size_x * x_fields,
                      field_size_y * y_fields)
        coordinates = (xmin, ymin, xmax, ymax)  # in m
        points = [
            (coordinates[0], coordinates[1]),  # xmin, ymin
            (coordinates[2], coordinates[1]),  # xmax, ymin
            (coordinates[0], coordinates[3]),  # xmin, ymax
            (coordinates[2], coordinates[3]),  # xmax, ymax
        ]

        # Create an ROA with the coordinates of the field.
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=0.0,
                        name=roa_name,
                        slice_index=0)
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                      self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                      self.ebeam_focus, roa, path=None, username="default", pre_calibrations=None,
                                      save_full_cells=False, settings_obs=None, spot_grid_thresh=0.5, blank_beam=True,
                                      stop_acq_on_failure=True, future=None)
        # Set the _pos_first_tile, which would normally be set in the run function.
        task._pos_first_tile = task.get_pos_first_tile()

        # Verify that for pre-calibrations compared to the top left corner of the ROA, the stage
        # position is located half a field to the top left (outside the ROA).
        task.field_idx = (-1, -1)  # (-1, -1) is the index where the pre-calibrations are performed

        # In the role='stage' coordinate system the x-axis points to the right and y-axis to the top.
        expected_position = (xmin - res_x / 2 * px_size_x,
                             ymax + res_x / 2 * px_size_y)  # [m]
        actual_position = task.get_abs_stage_movement()  # [m]
        numpy.testing.assert_allclose(actual_position, expected_position)
        # Verify that the position where the pre-calibration is performed, does not lie inside the ROA coordinates.
        self.assertFalse(is_point_in_rect(actual_position, coordinates))

    def test_pre_calibrate(self):
        """
        Test the ASM settings are unchanged after running the pre-calibrations, except the descanner scan offset.
        """
        try:
            import fastem_calibrations
        except ImportError as err:
            raise unittest.SkipTest(
                f"Skipping 'test_pre_calibrate', correct libraries to perform this test are not available.\n"
                f"Got the error: {err}")
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value

        x_fields = 5
        y_fields = 8

        # The coordinates of the ROA in meters.
        xmin, ymin, xmax, ymax = (0, 0, res_x * px_size_x * x_fields, res_y * px_size_y * y_fields)
        coordinates = (xmin, ymin, xmax, ymax)  # in m
        points = [
            (coordinates[0], coordinates[1]),  # xmin, ymin
            (coordinates[2], coordinates[1]),  # xmax, ymin
            (coordinates[0], coordinates[3]),  # xmin, ymax
            (coordinates[2], coordinates[3]),  # xmax, ymax
        ]

        # Create an ROA with the coordinates of the field.
        roa_name = "test_megafield_id"
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=0.0,
                        name=roa_name,
                        slice_index=0)
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                      self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                      self.ebeam_focus, roa, path=None, username="default", pre_calibrations=None,
                                      save_full_cells=False, settings_obs=None, spot_grid_thresh=0.5, blank_beam=True,
                                      stop_acq_on_failure=True, future=None)

        self.descanner.updateMetadata({model.MD_SCAN_GAIN: (5000, 5000)})

        # Set the _pos_first_tile, which would normally be set in the run function.
        task._pos_first_tile = task.get_pos_first_tile()

        asm_config_orig = fastem_calibrations.configure_hw.get_config_asm(self.multibeam, self.descanner, self.mppc)
        pre_calibrations = [Calibrations.OPTICAL_AUTOFOCUS, Calibrations.IMAGE_TRANSLATION_PREALIGN]
        try:
            task.pre_calibrate(pre_calibrations=pre_calibrations)
        except ValueError:
            # Handle optical autofocus calibration raised ValueError.
            # For now, pass and continue with the test.
            pass
        asm_config_current = fastem_calibrations.configure_hw.get_config_asm(self.multibeam, self.descanner, self.mppc)

        # Verify that all settings, except the descanner scan offset, stay the same after running the pre-calibrations.
        for component, settings in asm_config_current.items():
            for va, value in settings.items():
                if va == 'scanOffset' and component == 'descanner':
                    # image translation pre-alignment changes the descanner offset, therefore it has changed.
                    continue
                self.assertEqual(asm_config_orig[component][va], value)

    def test_get_pos_first_tile(self):
        """Test that the position of the first tile is calculated correctly for a varying number of fields."""
        res_x, res_y = self.multibeam.resolution.value  # single field size
        px_size_x, px_size_y = self.multibeam.pixelSize.value

        # Loop over different ROA sizes by varying the number of fields in x and y.
        for x_fields, y_fields in zip((1, 2, 40, 34, 5), (1, 22, 43, 104, 25)):
            # The coordinates of the ROA in meters.
            xmin, ymin, xmax, ymax = (0, 0, res_x * px_size_x * x_fields, res_y * px_size_y * y_fields)
            coordinates = (xmin, ymin, xmax, ymax)  # in m
            points = [
                (coordinates[0], coordinates[1]),  # xmin, ymin
                (coordinates[2], coordinates[1]),  # xmax, ymin
                (coordinates[0], coordinates[3]),  # xmin, ymax
                (coordinates[2], coordinates[3]),  # xmax, ymax
            ]

            # Create an ROA with the coordinates of the field.
            roa_name = "test_megafield_id"
            roa = FastEMROA(shape=MockEditableShape(),
                            main_data=self.main_data,
                            overlap=0.0,
                            name=roa_name,
                            slice_index=0)
            roa.shape._points = points
            roa.shape.points.value = points

            # Give sometime for calculation of field_indices
            time.sleep(2)

            task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                          self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                          self.ebeam_focus, roa, path=None, username="default", pre_calibrations=None,
                                          save_full_cells=False, settings_obs=None, spot_grid_thresh=0.5,
                                          blank_beam=True, stop_acq_on_failure=True, future=None)

            pos_first_tile_actual = task.get_pos_first_tile()

            # The position of the first tile is expected to be to the center position of the top left corner tile
            # of the ROA.
            pos_first_tile_expected = (xmin + res_x / 2 * px_size_x,
                                       ymax - res_y / 2 * px_size_y)
            self.assertEqual(pos_first_tile_actual, pos_first_tile_expected)

    def test_calibration_metadata(self):
        """Test the correct calibration metadata is returned."""
        original_md = self.mppc.getMetadata().get(model.MD_EXTRA_SETTINGS, "")

        # Create the settings observer to store the settings on the metadata
        settings_obs = SettingsObserver(model.getMicroscope(), model.getComponents())
        points = [(0, 0), (0, 0), (0, 0), (0, 0)]

        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=0.0,
                        name="roa_name",
                        slice_index=0)
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                      self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                      self.ebeam_focus, roa, path=None, username="default", pre_calibrations=None,
                                      save_full_cells=False, settings_obs=settings_obs, spot_grid_thresh=0.5,
                                      blank_beam=True, stop_acq_on_failure=True, future=None)

        # Test that _create_acquisition_metadata() sets the settings from the selection
        acquisition_md = task._create_acquisition_metadata()
        self.assertEqual(acquisition_md.keys(), SETTINGS_SELECTION.keys())

        # Verify that by getting the acquisition metadata, the correct metadata is set on the mppc.
        self.mppc.updateMetadata({model.MD_EXTRA_SETTINGS: ""})
        acquisition_md = task._create_acquisition_metadata()
        mppc_extra_settings = json.loads(self.mppc.getMetadata().get(model.MD_EXTRA_SETTINGS))
        self.assertEqual(acquisition_md.keys(), mppc_extra_settings.keys())

        # Set back original metadata on the mppc
        self.mppc.updateMetadata({model.MD_EXTRA_SETTINGS: original_md})

    @unittest.skipIf(TEST_NOHW, "Setting dataContent to full does not work in simulator")
    def test_save_full_cells(self):
        """Test saving fields with cell images of 900x900 px instead of 800x800 px"""
        data_content = self.mppc.dataContent.value
        self.mppc.dataContent.value = "full"

        # Create the settings observer to store the settings on the metadata
        settings_obs = SettingsObserver(model.getMicroscope(), model.getComponents())

        coordinates = (0, 0, 1e-8, 1e-8)  # in m
        roc_2 = fastem.FastEMROC("roc_2", 0, coordinates)
        roc_3 = fastem.FastEMROC("roc_3", 0, coordinates)
        points = [(0, 0), (0, 0), (0, 0), (0, 0)]

        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=0.06,
                        name="roa_name",
                        slice_index=0)
        roa.roc_2.value = roc_2
        roa.roc_3.value = roc_3
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(1)

        # Acquire an image were the full cell images are cropped to 800x800px
        task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                      self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                      self.ebeam_focus, roa, path="test-path", username="default",
                                      pre_calibrations=None, save_full_cells=False, settings_obs=settings_obs,
                                      spot_grid_thresh=0.5, blank_beam=True, stop_acq_on_failure=True, future=Mock())
        data, err = task.run()
        self.assertEqual(data[(0, 0)].shape, (6400, 6400))

        # Acquire an image were the cell images are 900x900px
        task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                      self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                      self.ebeam_focus, roa, path="test-path", username="default",
                                      pre_calibrations=None, save_full_cells=True, settings_obs=settings_obs,
                                      spot_grid_thresh=0.5, blank_beam=True, stop_acq_on_failure=True, future=Mock())
        data, err = task.run()
        self.assertEqual(data[(0, 0)].shape, (7200, 7200))

        # set back initial value
        self.mppc.dataContent.value = data_content

    def test_calculate_beam_shift_cor_indices(self):
        """Test calculating where to run the beamshift correction"""
        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=0.0,
                        name="roa_name",
                        slice_index=0)
        roa.field_indices = [(3, 0), (4, 0),
                             (2, 1), (3, 1), (4, 1), (10, 1),
                             (1, 2), (2, 2), (3, 2), (4, 2),
                             (0, 3), (1, 3), (2, 3), (3, 3), (4, 3)]
        task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                      self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                      self.ebeam_focus, roa, path=None, username="default", pre_calibrations=None,
                                      save_full_cells=False, settings_obs=None, spot_grid_thresh=0.5, blank_beam=True,
                                      stop_acq_on_failure=True, future=None)
        # When n_beamshifts=1, the beamshift correction should be applied for every field.
        # Therefore, the calculated indices should match the original field indices.
        beam_shift_indices = task._calculate_beam_shift_cor_indices(n_beam_shifts=1)
        self.assertEqual(beam_shift_indices, roa.field_indices)

        # When n_beamshifts=3, the beamshift correction should be applied every 3 sections.
        # If the 3rd section is not present in the fields to be acquired, select the next available field in the row.
        expected_field_indices = [(3, 0),
                                  (2, 1), (10, 1),
                                  (1, 2), (4, 2),
                                  (0, 3), (3, 3)]
        beam_shift_indices = task._calculate_beam_shift_cor_indices(n_beam_shifts=3)
        self.assertEqual(beam_shift_indices, expected_field_indices)

        # When n_beamshifts=5, the beamshift correction should be applied every 5 sections.
        # If the 5th section is not present in the fields to be acquired, select the next available field in the row.
        expected_field_indices = [(3, 0),
                                  (2, 1), (10, 1),
                                  (1, 2),
                                  (0, 3)]
        beam_shift_indices = task._calculate_beam_shift_cor_indices(n_beam_shifts=5)
        self.assertEqual(beam_shift_indices, expected_field_indices)


class TestFastEMAcquisitionTaskMock(TestFastEMAcquisitionTask):
    """Test the methods of fastem.AcquisitionTask without a backend and with mocked components."""

    @classmethod
    def setUpClass(cls):
        # If we are testing without hardware we just need a few attributes to be set correctly.
        cls.asm = None

        # Use Mocks of the classes to be able to call the fake VAs as for instance mppc.dataContent.value
        cls.mppc = Mock()
        cls.mppc.configure_mock(**{"getMetadata.return_value": {model.MD_USER: "test-user"}})
        cls.mppc.dataContent.value = 'empty'
        cls.mppc.frameDuration.value = 0.1
        cls.mppc.cellCompleteResolution.value = (900, 900)
        cls.mppc.shape = (8, 8)
        cls.mppc.configure_mock(
            **{"getMetadata.return_value": {model.MD_CALIB: {"pitch": DEFAULT_PITCH},}}
        )

        cls.multibeam = Mock()
        cls.multibeam.configure_mock(**{"getMetadata.return_value": {model.MD_SCAN_OFFSET_CALIB: [0.01, 0.01],
                                                                     model.MD_SCAN_AMPLITUDE_CALIB: [0.02, 0.02]}})
        cls.multibeam.pixelSize.value = (4.0e-9, 4.0e-9)
        cls.multibeam.resolution.value = (6400, 6400)

        cls.descanner = None
        cls.stage = Mock()
        cls.stage.axes = {
            "x": model.Axis(unit="m", range=(-100.0e-6, 100.0e-6)),
            "y": model.Axis(unit="m", range=(-100.0e-6, 100.0e-6)),
            "z": model.Axis(unit="m", range=(-100.0e-6, 100.0e-6)),
        }
        cls.scan_stage = Mock()
        cls.scan_stage.configure_mock(**{"getMetadata.return_value": {model.MD_ROTATION_COR: 0.0}})
        cls.scan_stage.position.value = {"x": 0, "y": 0, "z": 0}
        cls.scan_stage.axes = {
            "x": model.Axis(unit="m", range=(-100.0e-6, 100.0e-6)),
            "y": model.Axis(unit="m", range=(-100.0e-6, 100.0e-6)),
            "z": model.Axis(unit="m", range=(-100.0e-6, 100.0e-6)),
        }

        cls.scanner = Mock()
        cls.scanner.configure_mock(**{"getMetadata.return_value": {}})
        cls.scanner.blanker.value = True

        cls.ccd = Mock()
        image = numpy.zeros((256, 256))
        # set a grid of 8 by 8 points to 1
        image[54:150:12, 54:150:12] = 1
        image = model.DataArray(input_array=image)
        cls.ccd.data.configure_mock(**{"get.return_value": image})
        cls.ccd.configure_mock(**{"getMetadata.return_value": {
            model.MD_FAV_POS_ACTIVE: {"j": 100, "i": 100},
            model.MD_SENSOR_PIXEL_SIZE: (3.45e-6, 3.45e-6),
            model.MD_LENS_MAG: 40,
        }})
        cls.ccd.pointSpreadFunctionSize.value = 1
        cls.ccd.pixelSize.value = (1.0e-7, 1.0e-7)

        cls.lens = Mock()
        cls.lens.magnification.value = 10

        cls.se_detector = Mock()
        cls.ebeam_focus = Mock()

        cls.beamshift = Mock()
        cls.beamshift.shift.value = [1, 1]

        # Mock the FastEMMainGUIData
        cls.main_data = Mock()
        cls.main_data.current_sample.value = None
        cls.main_data.asm = cls.asm
        cls.main_data.multibeam = cls.multibeam
        cls.main_data.descanner = cls.descanner
        cls.main_data.mppc = cls.mppc

        # Mock fastem_calibrations and fastem_calibrations.util, to be able to call them on systems where
        # fastem_calibrations is not available.
        import sys
        from importlib import reload

        sys.modules['fastem_calibrations'] = Mock()
        sys.modules['fastem_calibrations'].util = Mock()
        sys.modules['fastem_calibrations'].util.configure_mock(**{"create_image_dir.return_value": ""})
        # Reload odemis.acq.fastem so that the mocked modules get imported.
        reload(fastem)

    @classmethod
    def tearDownClass(cls):
        # override the teardownclass of the base class
        return

    def test_save_full_cells(self):
        """Test saving fields with cell images of 900x900 px instead of 800x800 px"""
        coordinates = (0, 0, 1e-8, 1e-8)  # in m
        roc_2 = fastem.FastEMROC("roc_2", 0, coordinates)
        roc_3 = fastem.FastEMROC("roc_3", 0, coordinates)
        points = [(0, 0), (0.000001, 0), (0.000001, 0.000001), (0, 0.000001)]

        roa = FastEMROA(shape=MockEditableShape(),
                        main_data=self.main_data,
                        overlap=0.0,
                        name="roa_name",
                        slice_index=0)
        roa.roc_2.value = roc_2
        roa.roc_3.value = roc_3
        roa.shape._points = points
        roa.shape.points.value = points

        # Give sometime for calculation of field_indices
        time.sleep(2)

        # Acquire an image were the full cell images are cropped to 800x800px
        task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                      self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                      self.ebeam_focus, roa, path="test-path", username="default",
                                      pre_calibrations=None, save_full_cells=False, settings_obs=None,
                                      spot_grid_thresh=0.5, blank_beam=True, stop_acq_on_failure=True, future=Mock())

        # image_received should be called as a side effect of calling data.next, this signals that the data is received
        def _image_received(*args, **kwargs):
            # Create a fake image of ones, the multibeam resolution determines the image shape
            task.image_received(None, numpy.ones(self.multibeam.resolution.value))

        self.mppc.configure_mock(**{"data.next.side_effect": _image_received})

        data, err = task.run()
        self.assertEqual(data[(0, 0)].shape, (6400, 6400))

        # Acquire an image were the cell images are 900x900px
        task = fastem.AcquisitionTask(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage,
                                      self.scan_stage, self.ccd, self.beamshift, self.lens, self.se_detector,
                                      self.ebeam_focus, roa, path="test-path", username="default",
                                      pre_calibrations=None, save_full_cells=True, settings_obs=None,
                                      spot_grid_thresh=0.5, blank_beam=True, stop_acq_on_failure=True, future=Mock())

        data, err = task.run()
        self.assertEqual(data[(0, 0)].shape, (7200, 7200))

    def test_pre_calibrate(self):
        self.skipTest(
            "Skipping test because the pre-calibration method is not mocked."
        )

    def test_calibration_metadata(self):
        self.skipTest(
            "Skipping test because backend is required and json cannot open a mocked object."
        )


if __name__ == "__main__":
    unittest.main()
