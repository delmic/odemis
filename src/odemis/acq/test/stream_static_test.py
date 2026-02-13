#-*- coding: utf-8 -*-
"""
@author: Éric Piel

Copyright © 2013-2025 Éric Piel, Delmic

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

# Test acq.stream._static and acq.stream._projection classes

import logging
import os
import threading
import time
import unittest

import numpy

from odemis import model
from odemis.acq import stream, calibration
from odemis.acq.stream import RGBSpatialSpectrumProjection, \
    SinglePointSpectrumProjection, SinglePointTemporalProjection, \
    LineSpectrumProjection, MeanSpectrumProjection, POL_POSITIONS
from odemis.dataio import tiff
from odemis.model import MD_POL_NONE, MD_POL_HORIZONTAL, MD_POL_VERTICAL, \
    MD_POL_POSDIAG, MD_POL_NEGDIAG, MD_POL_RHC, MD_POL_LHC, DataArrayShadow, TINT_FIT_TO_RGB
from odemis.util import testing, img, spectrum

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

FILENAME = "test" + tiff.EXTENSIONS[0]


class StaticStreamsTestCase(unittest.TestCase):
    """
    Test static streams, which don't need any backend running.
    """

    def tearDown(self):
        # clean up
        try:
            os.remove(FILENAME)
        except Exception:
            pass

    def test_fluo(self):
        """Test StaticFluoStream"""
        md = {
            model.MD_DESCRIPTION: "green dye",
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
            model.MD_POS: (13.7e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1,  # s
            model.MD_IN_WL: (600e-9, 620e-9),  # m
            model.MD_OUT_WL: (620e-9, 650e-9),  # m
            model.MD_USER_TINT: (0, 0, 255),  # RGB (blue)
            model.MD_ROTATION: 0.1,  # rad
            model.MD_SHEAR: 0,
        }

        # DataArray
        da = model.DataArray(numpy.zeros((512, 1024), dtype=numpy.uint16), md)
        da[12] = 2 ** 11
        da[15] = 2 ** 10

        fls = stream.StaticFluoStream(md[model.MD_DESCRIPTION], da)
        pj = stream.RGBSpatialProjection(fls)

        self.assertEqual(fls.excitation.value, md[model.MD_IN_WL])
        self.assertEqual(fls.emission.value, md[model.MD_OUT_WL])
        self.assertEqual(tuple(fls.tint.value), md[model.MD_USER_TINT])

        time.sleep(0.5)  # wait a bit for the image to update
        im = pj.image.value
        self.assertEqual(im.shape, (512, 1024, 3))
        numpy.testing.assert_equal(im[0, 0], [0, 0, 0])
        numpy.testing.assert_equal(im[12, 1], md[model.MD_USER_TINT])

    def test_fluo_3d(self):
        """Test StaticFluoStream with Z-stack"""
        md = {
            model.MD_DESCRIPTION: "green dye",
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 1e-6, 10e-6),  # m/px
            model.MD_POS: (13.7e-3, -30e-3, 25e-3),  # m
            model.MD_EXP_TIME: 1,  # s
            model.MD_IN_WL: (600e-9, 620e-9),  # m
            model.MD_OUT_WL: (620e-9, 650e-9),  # m
            model.MD_USER_TINT: (0, 0, 255),  # RGB (blue)
            model.MD_ROTATION: 0.1,  # rad
            model.MD_SHEAR: 0,
        }

        # DataArray
        da = model.DataArray(numpy.zeros((10, 512, 1024), dtype=numpy.uint16), md)
        da[:, 10] = 2 ** 10  # bigger value (than 10)
        da[:, 11] = 2 ** 11  # largest value
        da[3, 12] = 2 ** 11  # largest value, but only at z == 3

        fls = stream.StaticFluoStream(md[model.MD_DESCRIPTION], da)
        pj = stream.RGBSpatialProjection(fls)

        # Change zIndex (from 0)
        fls.zIndex.value = 4
        fls.max_projection.value = False

        time.sleep(0.5)  # wait a bit for the image to update
        im = pj.image.value
        self.assertEqual(im.shape, (512, 1024, 3))
        numpy.testing.assert_equal(im[0, 0], [0, 0, 0])
        numpy.testing.assert_equal(im[11, 1], md[model.MD_USER_TINT])

        # check .getRawValue() works with 3D data
        self.assertEqual(pj.getRawValue((0, 0)), 0)
        self.assertEqual(pj.getRawValue((4, 11)), 2 ** 11)

        # Activate max_projection => column 12 is also at max intensity (from z == 3)
        fls.max_projection.value = True
        time.sleep(0.5)  # wait a bit for the image to update
        im = pj.image.value
        self.assertEqual(im.shape, (512, 1024, 3))
        numpy.testing.assert_equal(im[0, 0], [0, 0, 0])
        numpy.testing.assert_equal(im[11, 1], md[model.MD_USER_TINT])
        numpy.testing.assert_equal(im[12, 1], md[model.MD_USER_TINT])

        # check .getRawValue() works with 3D data
        self.assertEqual(pj.getRawValue((0, 0)), 0)
        self.assertEqual(pj.getRawValue((4, 11)), 2 ** 11)
        self.assertEqual(pj.getRawValue((4, 12)), 2 ** 11)

    def test_cl(self):
        """Test StaticCLStream"""
        # CL metadata
        md = {
            model.MD_SW_VERSION: "2.1",
            model.MD_HW_NAME: "pmt",
            model.MD_DESCRIPTION: "CL",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 16,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
            model.MD_POS: (1.2e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_OUT_WL: (658e-9, 845e-9),  # m
        }

        # CL DataArray
        da = model.DataArray(1500 + numpy.zeros((512, 1024), dtype=numpy.uint16), md)

        cls = stream.StaticCLStream("test", da)
        pj = stream.RGBSpatialProjection(cls)
        time.sleep(0.5)  # wait a bit for the image to update

        self.assertEqual(cls.emission.value, md[model.MD_OUT_WL])
        self.assertEqual(pj.image.value.shape, (512, 1024, 3))

    def test_small_hist(self):
        """Test small histogram computation"""
        # CL metadata
        md = {
            model.MD_SW_VERSION: "2.1",
            model.MD_HW_NAME: "pmt",
            model.MD_DESCRIPTION: "CL",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 16,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
            model.MD_POS: (1.2e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_OUT_WL: (658e-9, 845e-9),  # m
        }

        # DataArray with very big type, but very small values
        da = model.DataArray(numpy.zeros((512, 1024), dtype=numpy.uint32), md)
        da[2:100, 5:600] = 1
        da[3, :] = 2  # The whole line

        cls = stream.StaticCLStream("test", da)
        time.sleep(0.5)  # wait a bit for the image to update

        h = cls.histogram.value
        ir = cls.intensityRange.range[0][0], cls.intensityRange.range[1][1]

        self.assertEqual(ir[0], 0)
        self.assertGreaterEqual(ir[1], da.max())
        self.assertEqual(ir[1], 255)  # Rounded to the next power of 2 -1, starting from 255
        self.assertEqual(h[2], da.shape[1])  # how many values == 2: a whole line
        self.assertEqual((ir[1] - ir[0] + 1) % 256, 0, f"range {ir} doesn't have a length multiple of 256")

    def test_int_hist(self):
        """Test histogram computation with signed int"""
        md = {
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
            model.MD_POS: (1.2e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
        }

        # DataArray with very big type, but very small values
        da = model.DataArray(numpy.zeros((512, 1024), dtype=numpy.int16), md)
        # Use values near power of 2, so that it rounds up to that values
        da[2:100, 5:600] = -1000  # >= -1024
        da[3, :] = 8000  # <= 8191

        cls = stream.StaticCLStream("test", da)
        time.sleep(0.5)  # wait a bit for the image to update

        h = cls.histogram.value
        ir = cls.intensityRange.range[0][0], cls.intensityRange.range[1][1]

        self.assertEqual(ir[0], -1024) # rounded to the previous power of 2
        self.assertGreaterEqual(ir[1], da.max())
        self.assertEqual(ir[1], 8191)  # rounded to the next power of 2 -1
        self.assertEqual((ir[1] - ir[0] + 1) % 256, 0, f"range {ir} doesn't have a length multiple of 256")

    def test_uint16_hist(self):
        """Test histogram computation with int16, with a small width"""
        md = {
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
            model.MD_POS: (1.2e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
        }

        # DataArray with very big type, and only values very large
        da = model.DataArray(numpy.zeros((512, 1024), dtype=numpy.uint16), md) + 60000
        # Use values near each other so that 10% is preferred
        da[2:100, 5:600] += 1
        da[3, :] += 1000

        cls = stream.StaticCLStream("test", da)
        time.sleep(0.5)  # wait a bit for the image to update

        h = cls.histogram.value
        ir = cls.intensityRange.range[0][0], cls.intensityRange.range[1][1]

        self.assertTrue(59000 <= ir[0] <= da.min())  # round 10% less
        self.assertTrue(da.max() <= ir[1] <= 62000)  # rounded 10% more
        self.assertEqual((ir[1] - ir[0] + 1) % 256, 0, f"range {ir} doesn't have a length multiple of 256")


    def test_uint16_hist_white(self):
        """Test histogram computation with int16, with only a single value"""
        md = {
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
            model.MD_POS: (1.2e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
        }

        # DataArray with very big type, and only values very large
        da = model.DataArray(numpy.zeros((512, 1024), dtype=numpy.uint16), md)
        da[:] = 2**16 - 1 # max

        cls = stream.StaticCLStream("test", da)
        time.sleep(0.5)  # wait a bit for the image to update

        h = cls.histogram.value
        ir = cls.intensityRange.range[0][0], cls.intensityRange.range[1][1]

        self.assertGreaterEqual((ir[1] - ir[0] + 1), 256)
        self.assertEqual((ir[1] - ir[0] + 1) % 256, 0, f"range {ir} doesn't have a length multiple of 256")


    def _create_ar_data(self, shape, tweak=0, pol=None):
        """
        shape (200<int, 200<int)
        tweak (0<=int<20)
        pol (MD_POL_*)
        """
        metadata = {
            model.MD_SW_VERSION: "1.0-test",
            model.MD_HW_NAME: "fake ccd",
            model.MD_DESCRIPTION: "AR polarization analyzer",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6),  # m/px
            model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
            model.MD_POS: (1.2e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_AR_POLE: (253.1, 65.1),
            model.MD_LENS_MAG: 0.4,  # ratio
        }

        QWP_POSITIONS = {
            MD_POL_NONE: 1.6,
            MD_POL_HORIZONTAL: 0,
            MD_POL_VERTICAL: 1.570796,
            MD_POL_POSDIAG: 0.785398,
            MD_POL_NEGDIAG: 2.356194,
            MD_POL_RHC: 0,
            MD_POL_LHC: 0,
        }
        LINPOL_POSITIONS = {
            MD_POL_NONE: 1.6,
            MD_POL_HORIZONTAL: 0,
            MD_POL_VERTICAL: 1.570796,
            MD_POL_POSDIAG: 0.785398,
            MD_POL_NEGDIAG: 2.356194,
            MD_POL_RHC: 0.785398,
            MD_POL_LHC: 2.356194,
        }
        if pol:
            metadata[model.MD_POL_MODE] = pol
            metadata[model.MD_POL_POS_LINPOL] = QWP_POSITIONS[pol]  # rad
            metadata[model.MD_POL_POS_QWP] = LINPOL_POSITIONS[pol]  # rad

        center = shape[0] // 2, shape[1] // 2
        data = numpy.zeros(shape, dtype=numpy.uint16) + (1500 + 100 * tweak)
        # modify a few px close to AR_POLE
        data[center[0] - 10:center[0] + 70, center[1] - 200:center[1] - 100 + tweak * 10] += 1000 * (tweak + 1)
        return model.DataArray(data, metadata)

    def test_ar(self):
        """Test StaticARStream"""
        # AR metadata
        data0 = self._create_ar_data((512, 1024))
        data1 = self._create_ar_data((512, 1024), tweak=5)
        data1.metadata[model.MD_POS] = (1.5e-3, -30e-3)
        data1.metadata[model.MD_BASELINE] = 300  # AR background should take this into account

        logging.info("setting up stream")
        ars = stream.StaticARStream("test", [data0, data1])
        ars_raw_pj = stream.ARRawProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait()

        # Control AR projection
        im2d0 = ars_raw_pj.image.value
        time.sleep(0.5)  # wait shortly as .image is updated multiple times (maybe due to calc histogram)

        # Check it's a RGB DataArray
        self.assertEqual(im2d0.shape[2], 3)

        logging.info("changing AR pos")
        e.clear()
        # change position
        for p in ars.point.choices:
            if p != (None, None) and p != ars.point.value:
                ars.point.value = p
                break
        else:
            self.fail("Failed to find a second point in AR")

        e.wait(10.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times
        im2d1 = ars_raw_pj.image.value

        # Check it's a RGB DataArray
        self.assertEqual(im2d1.shape[2], 3)
        testing.assert_array_not_equal(im2d0, im2d1)

        logging.info("testing image background correction")
        # test background correction from image
        dcalib = numpy.ones((1, 1, 1, 512, 1024), dtype=numpy.uint16)
        calib = model.DataArray(dcalib, data0.metadata.copy())

        e.clear()
        ars.background.value = calib
        numpy.testing.assert_equal(ars.background.value, calib[0, 0, 0])

        e.wait(10.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times
        im2d2 = ars_raw_pj.image.value

        # Check it's a RGB DataArray
        self.assertEqual(im2d2.shape[2], 3)
        # check if the .image VA has been updated
        testing.assert_array_not_equal(im2d1, im2d2)

    def test_ar_das(self):
        """Test StaticARStream with a DataArrayShadow"""
        logging.info("setting up stream")
        data0 = self._create_ar_data((512, 1024))
        data1 = self._create_ar_data((512, 1024), tweak=5)
        data1.metadata[model.MD_POS] = (1.5e-3, -30e-3)
        data1.metadata[model.MD_BASELINE] = 300  # AR background should take this into account

        tiff.export(FILENAME, [data0, data1])
        acd = tiff.open_data(FILENAME)
        assert isinstance(acd.content[0], DataArrayShadow)

        ars = stream.StaticARStream("test", acd.content)
        ars_raw_pj = stream.ARRawProjection(ars)

        self.assertEqual(len(ars.point.choices), 3)  # 2 data + (None, None)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait()

        # Control AR projection
        im2d0 = ars_raw_pj.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d0.shape[2], 3)

    def test_arpol_allpol(self):
        """Test StaticARStream with ARRawProjection and all possible polarization modes."""
        # AR polarization analyzer data: different for each polarization
        pol_positions = [model.MD_POL_NONE] + list(POL_POSITIONS)
        data = []
        bg_data = []  # list of background images
        for i, pol in enumerate(pol_positions):
            data.append(self._create_ar_data((512, 1024), tweak=i, pol=pol))

            # Background images, per polarization
            dcalib = numpy.ones((1, 1, 1, 512, 1024), dtype=numpy.uint16) + i * 10
            calib = model.DataArray(dcalib, data[i].metadata.copy())
            bg_data.append(calib)

        logging.info("Setting up AR stream")
        ars = stream.StaticARStream("AR polarizer static stream", data)
        ars_raw_pj = stream.ARRawProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait(10.0)
        time.sleep(1.0)  # wait a little extra as .image is updated multiple times

        # Control AR projection
        im2d0 = ars_raw_pj.image.value
        self.assertEqual(im2d0.shape[2], 3)  # RGB DataArray

        # changing polarization position
        logging.info("Changing polarization position")
        e.clear()
        # Pick one random position (different from the current one)
        for p in ars_raw_pj.polarization.choices:
            if p != ars_raw_pj.polarization.value:
                ars_raw_pj.polarization.value = p
                break
        else:
            self.fail("Failed to find another polarization position")

        e.wait(10.0)
        time.sleep(1.0)  # wait extra, in case .image is updated multiple times (normally not here)
        im2d1 = ars_raw_pj.image.value

        self.assertEqual(im2d1.shape[2], 3)  # RGB DataArray
        # check that the .image VA has been updated
        testing.assert_array_not_equal(im2d0, im2d1)

        ###################################################################
        # testing background correction
        logging.info("Testing image background correction")
        # test background correction using a different image for each polarization position
        e.clear()
        ars.background.value = bg_data

        # test if bg VA shows same values as stored in bg_data
        for pol_pos in pol_positions:
            bg_VA = [bg_im for bg_im in ars.background.value if bg_im.metadata[model.MD_POL_MODE] == pol_pos]
            bg_im = [bg_im for bg_im in bg_data if bg_im.metadata[model.MD_POL_MODE] == pol_pos]
            self.assertEqual(len(bg_VA), 1)
            numpy.testing.assert_equal(bg_VA[0], bg_im[0][0, 0, 0])

        e.wait(10.0)
        time.sleep(1.0)  # wait shortly as .image is updated multiple times
        im2d2 = ars_raw_pj.image.value

        self.assertEqual(im2d2.shape[2], 3)  # RGB DataArray
        # check that the bg image has been applied, ie the .image VA has been updated
        testing.assert_array_not_equal(im2d1, im2d2)

        ###################################################################
        # 1 bg image but have six images -> should raise an error
        with self.assertRaises(ValueError):
            ars.background.value = bg_data[0]

    def test_arpol_1pol(self):
        """Test StaticARStream with ARRawProjection and one possible polarization mode."""
        # ARPOL data
        data = [self._create_ar_data((512, 1024), pol=MD_POL_HORIZONTAL)]

        logging.info("setting up stream")
        ars = stream.StaticARStream("test ar polarization static stream", data)
        ars_raw_pj = stream.ARRawProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait(10.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times

        im2d0 = ars_raw_pj.image.value

        # corresponding bg image
        bg_data = [model.DataArray(numpy.ones((1, 1, 1, 512, 1024), dtype=numpy.uint16), data[0].metadata)]
        e.clear()
        ars.background.value = bg_data
        # test if bg VA shows same values as stored in bg_data
        numpy.testing.assert_array_equal(ars.background.value[0], bg_data[0][0, 0, 0])
        e.wait(10.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times

        im2d1 = ars_raw_pj.image.value
        # check if the bg image has been applied and the .image VA has been updated
        testing.assert_array_not_equal(im2d0, im2d1)

    def test_arpolarimetry(self):
        """Test StaticARStream with ARPolarimetryProjection projection."""

        # Check whether the arpolarimetry package is available
        try:
            import arpolarimetry
        except ImportError:
            self.skipTest(
                "arpolarimetry package not available, type in a terminal: "
                "sudo apt install python-arpolarimetry python3-arpolarimetry")

        data_raw = []
        bg_data = []  # list of background images
        for i, pol in enumerate(POL_POSITIONS):
            data_raw.append(self._create_ar_data((512, 1024), tweak=i, pol=pol))

            # Background images, per polarization
            dcalib = numpy.ones((1, 1, 1, 512, 1024), dtype=numpy.uint16) + i * 10
            calib = model.DataArray(dcalib, data_raw[i].metadata.copy())
            bg_data.append(calib)

        logging.info("setting up AR stream")
        ars = stream.StaticARStream("test AR polarimetry static stream", data_raw)
        ars_vis_pol = stream.ARPolarimetryProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_vis_pol.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait(90.0)  # It can take a long time (especially since libqhull v8)
        assert e.is_set()
        time.sleep(1.0)  # wait shortly as .image is updated multiple times

        # Control AR projections
        img_vis_1 = ars_vis_pol.image.value

        # Check it's a RGB DataArray
        self.assertEqual(img_vis_1.shape[2], 3)

        # changing polarimetry position
        logging.info("changing polarimetry position")
        e.clear()
        # change position once
        for p in ars_vis_pol.polarimetry.choices:
            if p != (None, None) and p != ars_vis_pol.polarimetry.value:
                ars_vis_pol.polarimetry.value = p
                break
        else:
            self.fail("Failed to find another polarimetry position")

        e.wait(40.0)  # typically, it's fast, because all the positions are pre-computed
        assert e.is_set()

        img_vis_2 = ars_vis_pol.image.value

        # Check it's a RGB DataArray
        self.assertEqual(img_vis_2.shape[2], 3)
        # check if the .image VA has been updated
        testing.assert_array_not_equal(img_vis_1, img_vis_2)

        ###################################################################
        # testing background correction is applied visualized data
        logging.info("testing image background correction")

        e.clear()
        ars.background.value = bg_data

        e.wait(40.0)  # It can take a long time
        assert e.is_set()
        img_vis_3 = ars_vis_pol.image.value

        self.assertEqual(img_vis_3.shape[2], 3)  # RGB DataArray
        # check if the bg image has been applied and the .image VA has been updated
        testing.assert_array_not_equal(img_vis_2, img_vis_3)

    def test_ar_large_image(self):
        """Test StaticARStream with a large image to trigger resizing."""
        # AR metadata
        data = [self._create_ar_data((2000, 2200))]

        logging.info("setting up stream")
        ars = stream.StaticARStream("test AR static stream with large image", data)
        ars_raw_pj = stream.ARRawProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait(10.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times

        im2d0 = ars_raw_pj.image.value

        # corresponding bg image
        bg_data = [model.DataArray(numpy.ones((1, 1, 1, 2000, 2200), dtype=numpy.uint16), data[0].metadata)]
        e.clear()
        ars.background.value = bg_data
        # test if bg VA shows same values as stored in bg_data
        numpy.testing.assert_array_equal(ars.background.value[0], bg_data[0][0, 0, 0])
        e.wait(10.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times

        im2d1 = ars_raw_pj.image.value
        # check if the bg image has been applied and the .image VA has been updated
        testing.assert_array_not_equal(im2d0, im2d1)

    def _create_spectrum_data(self):
        """Create spectrum data."""
        data = numpy.ones((251, 1, 1, 200, 300), dtype="uint16")
        data[:, 0, 0, :, 3] = numpy.arange(200)
        data[:, 0, 0, :, 3] *= 3
        data[2, :, :, :, :] = numpy.arange(300)
        data[200, 0, 0, 2] = numpy.arange(300)
        wld = 433e-9 + numpy.arange(data.shape[0]) * 0.1e-9
        # spectrum metadata
        md = {model.MD_SW_VERSION: "1.0-test",
              model.MD_HW_NAME: "fake ccd",
              model.MD_DESCRIPTION: "Spectrum",
              model.MD_ACQ_DATE: time.time(),
              model.MD_BPP: 12,
              model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
              model.MD_POS: (1.2e-3, -30e-3),  # m
              model.MD_EXP_TIME: 0.2,  # s
              model.MD_LENS_MAG: 60,  # ratio
              model.MD_WL_LIST: wld,
              }
        return model.DataArray(data, md)

    def test_spectrum_das(self):
        """Test StaticSpectrumStream with DataArrayShadow"""
        # TODO: once it supports it, test the stream with pyramidal data
        spec = self._create_spectrum_data()
        tiff.export(FILENAME, spec)
        acd = tiff.open_data(FILENAME)

        specs = stream.StaticSpectrumStream("test", acd.content[0])
        proj_spatial = RGBSpatialSpectrumProjection(specs)
        time.sleep(0.5)  # wait a bit for the image to update

        # Control spatial spectrum
        im2d = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d.shape, spec.shape[-2:] + (3,))
        # Check it's at the right position
        md2d = im2d.metadata
        self.assertEqual(md2d[model.MD_POS], spec.metadata[model.MD_POS])

    def test_spectrum_2d(self):
        """Test StaticSpectrumStream 2D"""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test", spec)
        proj_spatial = RGBSpatialSpectrumProjection(specs)
        time.sleep(0.5)  # wait a bit for the image to update

        # Control spatial spectrum
        im2d = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d.shape, spec.shape[-2:] + (3,))
        # Check it's at the right position
        md2d = im2d.metadata
        self.assertEqual(md2d[model.MD_POS], spec.metadata[model.MD_POS])

        # change bandwidth to max
        specs.spectrumBandwidth.value = (specs.spectrumBandwidth.range[0][0],
                                         specs.spectrumBandwidth.range[1][1])
        time.sleep(0.5)  # wait a bit for the image to update
        im2d = proj_spatial.image.value
        self.assertEqual(im2d.shape, spec.shape[-2:] + (3,))

        # Check RGB spatial projection
        time.sleep(0.2)
        specs.tint.value = TINT_FIT_TO_RGB
        time.sleep(0.5)  # wait a bit for the image to update
        im2d = proj_spatial.image.value
        self.assertEqual(im2d.shape, spec.shape[-2:] + (3,))

    def test_spectrum_0d(self):
        """Test StaticSpectrumStream 0D"""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test", spec)
        proj_point_spectrum = SinglePointSpectrumProjection(specs)
        time.sleep(0.5)  # wait a bit for the image to update

        # Check 0D spectrum
        specs.selected_pixel.value = (1, 1)
        time.sleep(0.5)  # wait a bit for the image to update
        sp0d = proj_point_spectrum.image.value
        wl0d, _ = spectrum.get_spectrum_range(sp0d)
        self.assertEqual(sp0d.shape[0], spec.shape[0])
        self.assertEqual(wl0d.shape[0], spec.shape[0])
        self.assertEqual(sp0d.dtype, spec.dtype)
        self.assertTrue(numpy.all(sp0d <= spec.max()))

        # Check width > 1 (on the border)
        specs.selectionWidth.value = 12
        time.sleep(1.0)  # wait a bit for the image to update
        sp0d = proj_point_spectrum.image.value
        wl0d, _ = spectrum.get_spectrum_range(sp0d)
        self.assertEqual(len(sp0d), spec.shape[0])
        self.assertEqual(len(wl0d), spec.shape[0])
        self.assertIsInstance(sp0d.dtype.type(), numpy.floating)
        self.assertTrue(numpy.all(sp0d <= spec.max()))

        # Check with very large width
        specs.selectionWidth.value = specs.selectionWidth.range[1]
        specs.selected_pixel.value = (55, 106)
        time.sleep(1.0)  # wait a bit for the image to update
        sp0d = proj_point_spectrum.image.value
        wl0d, _ = spectrum.get_spectrum_range(sp0d)
        self.assertEqual(len(sp0d), spec.shape[0])
        self.assertEqual(len(wl0d), spec.shape[0])
        self.assertIsInstance(sp0d.dtype.type(), numpy.floating)
        self.assertTrue(numpy.all(sp0d <= spec.max()))

    def test_spectrum_1d(self):
        """Test StaticSpectrumStream 1D"""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test", spec)
        proj_line_spectrum = LineSpectrumProjection(specs)

        # Check 1d spectrum on corner-case: parallel to the X axis.
        # We select line #3, from px 7 until px 65 (included)
        specs.selected_line.value = [(3, 7), (3, 65)]
        time.sleep(1.0)  # ensure that .image is updated
        sp1d = proj_line_spectrum.image.value
        wl1d, u1d = spectrum.get_spectrum_range(sp1d)
        self.assertEqual(u1d, "m")
        self.assertEqual(sp1d.ndim, 3)
        self.assertEqual(sp1d.shape, (65 - 7 + 1, spec.shape[0], 3))
        self.assertEqual(sp1d.dtype, numpy.uint8)
        self.assertEqual(wl1d.shape, (spec.shape[0],))
        self.assertEqual(sp1d.metadata[model.MD_PIXEL_SIZE][1],
                         spec.metadata[model.MD_PIXEL_SIZE][0])

        # compare to doing it manually, which is easy as it's an horizontal line
        sp1d_raw_ex = spec[:, 0, 0, 7:66, 3]
        # make it contiguous to be sure to get the fast conversion, because
        # there are (still) some minor differences with the slow conversion
        sp1d_raw_ex = numpy.ascontiguousarray(sp1d_raw_ex.swapaxes(0, 1))

        # Need to convert to RGB to compare
        hist, edges = img.histogram(sp1d_raw_ex)
        irange = img.findOptimalRange(hist, edges, 1 / 256)
        sp1d_rgb_ex = img.DataArray2RGB(sp1d_raw_ex, irange)
        numpy.testing.assert_equal(sp1d, sp1d_rgb_ex)

        # Check 1d spectrum in diagonal
        specs.selected_line.value = [(30, 65), (1, 1)]
        time.sleep(1.0)  # ensure that .image is updated
        sp1d = proj_line_spectrum.image.value
        wl1d, _ = spectrum.get_spectrum_range(sp1d)
        self.assertEqual(sp1d.ndim, 3)
        # There is not too much expectations on the size of the spatial axis
        self.assertTrue(29 <= sp1d.shape[0] <= (64 * 1.41))
        self.assertEqual(sp1d.shape[1], spec.shape[0])
        self.assertEqual(sp1d.shape[2], 3)
        self.assertEqual(sp1d.dtype, numpy.uint8)
        self.assertEqual(wl1d.shape, (spec.shape[0],))
        self.assertGreaterEqual(sp1d.metadata[model.MD_PIXEL_SIZE][1],
                                spec.metadata[model.MD_PIXEL_SIZE][0])

        # Check 1d with larger width
        specs.selected_line.value = [(30, 65), (5, 1)]
        specs.selectionWidth.value = 12
        time.sleep(1.0)  # ensure that .image is updated
        sp1d = proj_line_spectrum.image.value
        wl1d, _ = spectrum.get_spectrum_range(sp1d)
        self.assertEqual(sp1d.ndim, 3)
        # There is not too much expectations on the size of the spatial axis
        self.assertTrue(29 <= sp1d.shape[0] <= (64 * 1.41))
        self.assertEqual(sp1d.shape[1], spec.shape[0])
        self.assertEqual(sp1d.shape[2], 3)
        self.assertEqual(sp1d.dtype, numpy.uint8)
        self.assertEqual(wl1d.shape, (spec.shape[0],))

        specs.selected_line.value = [(30, 65), (5, 12)]
        specs.selectionWidth.value = 13  # brings bad luck?
        time.sleep(1.0)  # ensure that .image is updated
        sp1d = proj_line_spectrum.image.value
        wl1d, _ = spectrum.get_spectrum_range(sp1d)
        self.assertEqual(sp1d.ndim, 3)
        # There is not too much expectations on the size of the spatial axis
        self.assertTrue(29 <= sp1d.shape[0] <= (53 * 1.41))
        self.assertEqual(sp1d.shape[1], spec.shape[0])
        self.assertEqual(sp1d.shape[2], 3)
        self.assertEqual(sp1d.dtype, numpy.uint8)
        self.assertEqual(wl1d.shape, (spec.shape[0],))

        # Check the raw data is a float
        sp1d_raw = proj_line_spectrum.projectAsRaw()
        self.assertIsInstance(sp1d_raw.dtype.type(), numpy.floating)

    def test_spectrum_calib_bg(self):
        """Test Static Spectrum Stream calibration and background image correction
        with spectrum data."""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test spectrum calibration and bg corr", spec)
        proj_spatial = RGBSpatialSpectrumProjection(specs)
        specs.spectrumBandwidth.value = (specs.spectrumBandwidth.range[0][0], specs.spectrumBandwidth.range[1][1])
        time.sleep(0.5)  # ensure that .image is updated

        # get current image without any correction
        prev_im2d = proj_spatial.image.value

        # create background image
        dbckg = numpy.ones(spec.shape, dtype=numpy.uint16) + 10
        wl_bckg = list(spec.metadata[model.MD_WL_LIST])
        obckg = model.DataArray(dbckg, metadata={model.MD_WL_LIST: wl_bckg})
        bckg = calibration.get_spectrum_data([obckg])

        # create spectrum efficiency correction
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 1.3, 6, 9.1], dtype=float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.arange(dcalib.shape[0]) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        # apply spectrum efficiency correction
        specs.efficiencyCompensation.value = calib

        time.sleep(0.5)
        im2d_effcorr = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d_effcorr.shape, spec.shape[-2:] + (3,))
        # check image different from previous image after bg correction, and different from efficiency corr. image
        testing.assert_array_not_equal(im2d_effcorr, prev_im2d)

        # apply background image correction
        specs.background.value = bckg

        time.sleep(0.5)
        im2d_bgcorr = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d_bgcorr.shape, spec.shape[-2:] + (3,))
        # check image different from previous image after bg correction, and different from efficiency corr. image
        testing.assert_array_not_equal(im2d_bgcorr, im2d_effcorr)
        testing.assert_array_not_equal(im2d_bgcorr, prev_im2d)

    def _create_temporal_spectrum_data(self):
        """Create temporal spectrum data."""
        data = numpy.random.randint(1, 100, size=(256, 128, 1, 20, 30), dtype="uint16")
        wld = 433e-9 + model.DataArray(numpy.arange(data.shape[0])) * 0.1e-9
        tld = model.DataArray(numpy.arange(data.shape[1])) * 0.1e-9
        # temporal spectrum data
        md = {model.MD_SW_VERSION: "1.0-test",
              model.MD_HW_NAME: "fake ccd",
              model.MD_DESCRIPTION: "Temporal spectrum",
              model.MD_DIMS: "CTZYX",
              model.MD_ACQ_DATE: time.time(),
              model.MD_BPP: 12,
              model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
              model.MD_POS: (1.2e-3, -30e-3),  # m
              model.MD_EXP_TIME: 0.2,  # s
              model.MD_LENS_MAG: 60,  # ratio
              model.MD_STREAK_MODE: True,
              model.MD_STREAK_TIMERANGE: 1e-9,  # s
              model.MD_WL_LIST: wld,
              model.MD_TIME_LIST: tld,
              }
        return model.DataArray(data, md)

    def test_temporal_spectrum(self):
        """Test StaticSpectrumStream and Projections for temporal spectrum data."""
        temporalspectrum = self._create_temporal_spectrum_data()
        tss = stream.StaticSpectrumStream("test temporal spectrum", temporalspectrum)
        time.sleep(1.0)  # wait a bit for the image to update

        # Control spatial spectrum
        proj_spatial = RGBSpatialSpectrumProjection(tss)
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d.shape, temporalspectrum.shape[-2:] + (3,))
        # Check it's at the right position
        md2d = im2d.metadata
        self.assertEqual(md2d[model.MD_POS], temporalspectrum.metadata[model.MD_POS])

        # change bandwidth to max
        tss.spectrumBandwidth.value = (tss.spectrumBandwidth.range[0][0], tss.spectrumBandwidth.range[1][1])
        time.sleep(0.2)
        im2d = proj_spatial.image.value
        self.assertEqual(im2d.shape, temporalspectrum.shape[-2:] + (3,))

        # Check RGB spatial projection
        tss.tint.value = TINT_FIT_TO_RGB
        time.sleep(0.2)
        im2d = proj_spatial.image.value
        self.assertEqual(im2d.shape, temporalspectrum.shape[-2:] + (3,))

        # Create projections
        proj_point_spectrum = SinglePointSpectrumProjection(tss)
        proj_point_chrono = SinglePointTemporalProjection(tss)

        # Test projections show correct data for different ebeam positions
        tl = temporalspectrum.metadata.get(model.MD_TIME_LIST)
        wl = temporalspectrum.metadata.get(model.MD_WL_LIST)

        for time_index in range(0, 3):
            for wl_index in range(0, 3):
                for x in range(0, 3):
                    for y in range(0, 3):
                        tss.selected_pixel.value = (x, y)
                        tss.selected_time.value = tl[time_index]
                        tss.selected_wavelength.value = wl[wl_index]
                        time.sleep(0.2)
                        self.assertListEqual(proj_point_spectrum.image.value.tolist(),
                                             temporalspectrum[:, time_index, 0, y, x].tolist())
                        self.assertListEqual(proj_point_chrono.image.value.tolist(),
                                             temporalspectrum[wl_index, :, 0, y, x].tolist())

    def test_temporal_spectrum_calib_bg(self):
        """Test StaticSpectrumStream calibration and background image correction
         with temporal spectrum data."""
        temporalspectrum = self._create_temporal_spectrum_data()
        tss = stream.StaticSpectrumStream("test temporal spectrum calibration and bg corr", temporalspectrum)
        proj_spatial = RGBSpatialSpectrumProjection(tss)
        tss.spectrumBandwidth.value = (tss.spectrumBandwidth.range[0][0], tss.spectrumBandwidth.range[1][1])

        time.sleep(0.5)  # ensure that .image is updated

        # get current image without any correction
        prev_im2d = proj_spatial.image.value

        # create bg image (C, T, 1, 1, 1)
        dbckg = numpy.ones(temporalspectrum.shape, dtype=numpy.uint16) + 10
        wl_bckg = list(temporalspectrum.metadata[model.MD_WL_LIST])
        bckg = model.DataArray(dbckg, metadata={model.MD_WL_LIST: wl_bckg,
                                                model.MD_STREAK_MODE: True,
                                                model.MD_STREAK_TIMERANGE: 1e-9,  # s
                                                })  # background data is 2D

        # create spectrum efficiency compensation file (C, 1, 1, 1, 1)
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 1.3, 6, 9.1], dtype=float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.array(range(dcalib.shape[0])) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        # apply spectrum efficiency compensation
        tss.efficiencyCompensation.value = calib

        time.sleep(0.5)
        im2d_effcorr = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d_effcorr.shape, temporalspectrum.shape[-2:] + (3,))
        # check image different from previous image after efficiency correction
        testing.assert_array_not_equal(im2d_effcorr, prev_im2d)

        # apply bg correction
        tss.background.value = bckg

        time.sleep(0.5)
        im2d_bgcorr = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d_bgcorr.shape, temporalspectrum.shape[-2:] + (3,))
        # check image different from previous image after bg correction, and different from efficiency corr. image
        testing.assert_array_not_equal(im2d_bgcorr, im2d_effcorr)
        testing.assert_array_not_equal(im2d_bgcorr, prev_im2d)

    def test_temporal_spectrum_false_calib_bg(self):
        """Test StaticSpectrumStream background image correction
         with temporal spectrum data using invalid bg images and calibration files."""
        temporalspectrum = self._create_temporal_spectrum_data()
        tss = stream.StaticSpectrumStream("test temporal spectrum calibration and bg corr", temporalspectrum)
        proj_spatial = RGBSpatialSpectrumProjection(tss)
        tss.spectrumBandwidth.value = (tss.spectrumBandwidth.range[0][0], tss.spectrumBandwidth.range[1][1])
        time.sleep(0.5)  # ensure that .image is updated

        # get current image without any correction
        prev_im2d = proj_spatial.image.value

        # create bg image (C, T, 1, 1, 1)
        dbckg = numpy.ones(temporalspectrum.shape, dtype=numpy.uint16) + 10
        wl_bckg = list(temporalspectrum.metadata[model.MD_WL_LIST])
        bckg = model.DataArray(dbckg, metadata={model.MD_WL_LIST: wl_bckg,
                                                model.MD_STREAK_MODE: True,
                                                model.MD_STREAK_TIMERANGE: 1e-9,  # s
                                                })  # background data is 2D

        # create spectrum efficiency compensation file (C, 1, 1, 1, 1)
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 1.3, 6, 9.1], dtype=float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.array(range(dcalib.shape[0])) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        time.sleep(0.5)

        # apply bg correction: should fail as streak mode of bg image and data different
        bckg.metadata[model.MD_STREAK_MODE] = False  # no time info
        with self.assertRaises(ValueError):
            tss.background.value = bckg
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

        # apply bg correction: should fail as time range of bg image and data different
        bckg.metadata[model.MD_STREAK_MODE] = True
        bckg.metadata[model.MD_STREAK_TIMERANGE] = 2e-9  # different time info
        with self.assertRaises(ValueError):
            tss.background.value = bckg
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

        # apply bg correction: should fail as bg image has no wl info (mirror mode), but data does
        bckg.metadata[model.MD_STREAK_TIMERANGE] = 1e-9
        del bckg.metadata[model.MD_WL_LIST]
        with self.assertRaises(ValueError):
            tss.background.value = bckg
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

        # test rejected backgrounds for data with no wl info
        # apply bg correction: should fail as bg image has wl info, but data not
        del temporalspectrum.metadata[model.MD_WL_LIST]  # no wl info in data
        bckg.metadata[model.MD_WL_LIST] = wl_bckg
        with self.assertRaises(ValueError):
            tss.background.value = bckg
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

        # apply efficiency correction, bg image still there: should fail as data has no wl info
        del bckg.metadata[model.MD_WL_LIST]
        with self.assertRaises(ValueError):
            tss.efficiencyCompensation.value = calib
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

    def _create_chronograph_data(self):
        """Create chronograph (time correlator) data."""
        data = numpy.random.randint(1, 100, size=(1, 128, 1, 20, 30), dtype="uint16")
        tld = model.DataArray(numpy.arange(data.shape[1])) * 0.1e-9
        # temporal spectrum data
        md = {model.MD_SW_VERSION: "1.0-test",
              model.MD_HW_NAME: "fake ccd",
              model.MD_DESCRIPTION: "Temporal spectrum",
              model.MD_DIMS: "CTZYX",
              model.MD_ACQ_DATE: time.time(),
              model.MD_BPP: 12,
              model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
              model.MD_POS: (1.2e-3, -30e-3),  # m
              model.MD_EXP_TIME: 0.2,  # s
              model.MD_LENS_MAG: 60,  # ratio
              model.MD_TIME_LIST: tld,
              }
        return model.DataArray(data, md)

    def test_chronograph(self):
        """Test StaticSpectrumStream and Projections for chronograph (time correlator) data."""
        chronograph = self._create_chronograph_data()
        cs = stream.StaticSpectrumStream("test chronograph", chronograph)
        time.sleep(1.0)  # wait a bit for the image to update

        # Control spatial spectrum
        proj_spatial = RGBSpatialSpectrumProjection(cs)
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d.shape, chronograph.shape[-2:] + (3,))
        # Check it's at the right position
        md2d = im2d.metadata
        self.assertEqual(md2d[model.MD_POS], chronograph.metadata[model.MD_POS])

        # Create projections
        proj_point_chrono = SinglePointTemporalProjection(cs)

        # Test projection shows correct data for different ebeam positions
        for x in range(0, 3):
            for y in range(0, 3):
                cs.selected_pixel.value = (x, y)
                time.sleep(0.2)
                self.assertListEqual(proj_point_chrono.image.value.tolist(),
                                     chronograph[0, :, 0, y, x].tolist())

    def test_chronograph_calib_bg(self):
        """Test StaticSpectrumStream calibration and background image correction
         with time correlator data.

         !!!For now we do not support this!!! Adapt test cases when changed!

         """
        chronograph = self._create_chronograph_data()
        cs = stream.StaticSpectrumStream("test chronograph calibration and bg corr", chronograph)
        proj_spatial = RGBSpatialSpectrumProjection(cs)
        time.sleep(0.5)  # ensure that .image is updated

        # get current image
        prev_im2d = proj_spatial.image.value

        # create bg image (C, T, 1, 1, 1)
        dbckg = numpy.ones(chronograph.shape, dtype=numpy.uint16) + 10
        time_bckg = list(chronograph.metadata[model.MD_TIME_LIST])
        bckg = model.DataArray(dbckg, metadata={model.MD_TIME_LIST: time_bckg})  # background data is 1D

        # create spectrum efficiency compensation file (C, 1, 1, 1, 1)
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 1.3, 6, 9.1], dtype=float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.array(range(dcalib.shape[0])) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        # apply spectrum efficiency compensation  -> should fail
        with self.assertRaises(ValueError):
            cs.efficiencyCompensation.value = calib

        # apply bg correction -> should fail - not supported yet!!!
        with self.assertRaises(ValueError):
            cs.background.value = bckg

        time.sleep(0.5)
        im2d = proj_spatial.image.value
        # check image still the same
        self.assertTrue(numpy.any(im2d == prev_im2d))

    def test_mean_spectrum(self):
        """Test MeanSpectrumStream for histogram display in settings panel
        with spectrum data."""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test spectrum mean", spec)
        proj = MeanSpectrumProjection(specs)
        time.sleep(2)
        mean_spec = proj.image.value
        self.assertEqual(mean_spec.shape, (spec.shape[0],))

    def test_mean_temporal_spectrum(self):
        """Test MeanSpectrumStream for histogram display in settings panel
        with temporal spectrum data."""
        temporalspectrum = self._create_temporal_spectrum_data()
        tss = stream.StaticSpectrumStream("test temporal spectrum mean", temporalspectrum)
        proj = MeanSpectrumProjection(tss)
        time.sleep(1.0)
        mean_temporalspectrum = proj.image.value
        self.assertEqual(mean_temporalspectrum.shape, (temporalspectrum.shape[0],))

    def test_mean_chronograph(self):
        """Test MeanSpectrumStream for histogram display in settings panel
        with time correlator data data."""
        chronograph = self._create_chronograph_data()
        cs = stream.StaticSpectrumStream("test chronograph mean", chronograph)
        proj = MeanSpectrumProjection(cs)
        time.sleep(2)
        mean_chronograph = proj.image.value
        self.assertEqual(mean_chronograph.shape, (chronograph.shape[0],))

    def test_tiled_stream(self):
        POS = (5.0, 7.0)
        size = (2000, 1000)
        md = {
            model.MD_DIMS: 'YX',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        arr = numpy.arange(size[0] * size[1], dtype=numpy.uint8).reshape(size[::-1])
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        ss = stream.StaticSEMStream("test", acd.content[0])
        pj = stream.RGBSpatialProjection(ss)

        # out of bounds
        with self.assertRaises(IndexError):
            pj.mpp.value = 1.0
        pj.mpp.value = 2e-6  # second zoom level

        # out of bounds
        with self.assertRaises(IndexError):
            pj.rect.value = (0.0, 0.0, 10e10, 10e10)
        # full image
        pj.rect.value = (POS[0] - 0.001, POS[1] - 0.0005, POS[0] + 0.001, POS[1] + 0.0005)

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(len(pj.image.value), 4)
        self.assertEqual(len(pj.image.value[0]), 2)
        # the corner tile should be smaller
        self.assertEqual(pj.image.value[3][1].shape, (244, 232, 3))

        # half image
        pj.rect.value = (POS[0] - 0.001, POS[1], POS[0], POS[1] + 0.0005)

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 1)

    def test_rgb_tiled_stream(self):
        POS = (5.0, 7.0)
        size = (2000, 1000, 3)
        md = {
            model.MD_DIMS: 'YXC',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        arr_shape = (1000, 2000, 3)
        arr = numpy.arange(size[0] * size[1] * size[2], dtype=numpy.uint8).reshape(arr_shape)
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        ss = stream.RGBStream("test", acd.content[0])
        pj = stream.RGBSpatialProjection(ss)

        # out of bounds
        with self.assertRaises(IndexError):
            pj.mpp.value = 1.0
        pj.mpp.value = 2e-6  # second zoom level

        # out of bounds
        with self.assertRaises(IndexError):
            pj.rect.value = (0.0, 0.0, 10e10, 10e10)

        # full image
        pj.rect.value = (POS[0] - 0.001, POS[1] - 0.0005, POS[0] + 0.001, POS[1] + 0.0005)

        # Wait a little bit to make sure the image has been generated
        time.sleep(1.0)
        self.assertEqual(len(pj.image.value), 4)
        self.assertEqual(len(pj.image.value[0]), 2)
        # the corner tile should be smaller
        self.assertEqual(pj.image.value[3][1].shape, (244, 232, 3))

        # half image
        pj.rect.value = (POS[0] - 0.001, POS[1], POS[0], POS[1] + 0.0005)

        # Wait a little bit to make sure the image has been generated
        time.sleep(1.0)
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 1)

    def test_rgb_tiled_stream_pan(self):
        read_tiles = []

        def getTileMock(self, x, y, zoom):
            tile_desc = "(%d, %d), z: %d" % (x, y, zoom)
            read_tiles.append(tile_desc)
            return tiff.DataArrayShadowPyramidalTIFF._getTileOldSP(self, x, y, zoom)

        tiff.DataArrayShadowPyramidalTIFF._getTileOldSP = tiff.DataArrayShadowPyramidalTIFF.getTile
        tiff.DataArrayShadowPyramidalTIFF.getTile = getTileMock

        POS = (5.0, 7.0)
        size = (3000, 2000, 3)
        md = {
            model.MD_DIMS: 'YXC',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        arr_shape = (2000, 3000, 3)
        arr = numpy.arange(size[0] * size[1] * size[2], dtype=numpy.uint8).reshape(arr_shape)
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        ss = stream.RGBStream("test", acd.content[0])
        pj = stream.RGBSpatialProjection(ss)
        time.sleep(0.5)

        # the maxzoom image has 2 tiles. So far 4 was read: 2 on the constructor, for
        # _updateHistogram and _updateDRange. And 2 for _updateImage, because .rect
        # and .mpp are initialized to the maxzoom image
        self.assertEqual(4, len(read_tiles))

        full_image_rect = (POS[0] - 0.0015, POS[1] - 0.001, POS[0] + 0.0015, POS[1] + 0.001)

        pj.mpp.value = 2e-6  # second zoom level
        # full image
        pj.rect.value = full_image_rect
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(28, len(read_tiles))
        self.assertEqual(len(pj.image.value), 6)
        self.assertEqual(len(pj.image.value[0]), 4)

        # half image (left side), all tiles are cached
        pj.rect.value = (POS[0] - 0.0015, POS[1] - 0.001, POS[0], POS[1] + 0.001)
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(28, len(read_tiles))
        self.assertEqual(len(pj.image.value), 3)
        self.assertEqual(len(pj.image.value[0]), 4)

        # half image (right side), only the center tiles will are cached
        pj.rect.value = (POS[0], POS[1] - 0.001, POS[0] + 0.0015, POS[1] + 0.001)
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(40, len(read_tiles))
        self.assertEqual(len(pj.image.value), 4)
        self.assertEqual(len(pj.image.value[0]), 4)

        # really small rect on the center, the tile is in the cache
        pj.rect.value = (POS[0], POS[1] - 0.00001, POS[0] + 0.00001, POS[1])

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(40, len(read_tiles))
        self.assertEqual(len(pj.image.value), 1)
        self.assertEqual(len(pj.image.value[0]), 1)

        # rect out of the image
        with self.assertRaises(IndexError):  # "rect out of bounds"
            pj.rect.value = (POS[0] - 15, POS[1] - 15, POS[0] + 16, POS[1] + 16)
            # Wait a little bit to make sure the image has been generated
            time.sleep(0.5)

        # get the old function back to the class
        tiff.DataArrayShadowPyramidalTIFF.getTile = tiff.DataArrayShadowPyramidalTIFF._getTileOldSP

    def test_rgb_tiled_stream_zoom(self):
        read_tiles = []

        def getTileMock(self, x, y, zoom):
            tile_desc = "(%d, %d), z: %d" % (x, y, zoom)
            read_tiles.append(tile_desc)
            return tiff.DataArrayShadowPyramidalTIFF._getTileOldSZ(self, x, y, zoom)

        tiff.DataArrayShadowPyramidalTIFF._getTileOldSZ = tiff.DataArrayShadowPyramidalTIFF.getTile
        tiff.DataArrayShadowPyramidalTIFF.getTile = getTileMock

        POS = (5.0, 7.0)
        dtype = numpy.uint8
        md = {
            model.MD_DIMS: 'YXC',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        num_cols = 3000
        num_rows = 2000
        arr_shape = (num_rows, num_cols, 3)
        arr = numpy.zeros(arr_shape, dtype=dtype)

        line = numpy.linspace(0, 255, num_cols, dtype=dtype)
        column = numpy.linspace(0, 255, num_rows, dtype=dtype)

        # Create a gradient, with red horizontally, and green vertically. Blue is 0 everywhere.
        # each row has values from 0 to 255, linearly distributed
        arr[:, :, 0] = numpy.tile(line, (num_rows, 1))
        # each column has values from 0 to 255, linearly distributed
        arr[:, :, 1] = numpy.tile(column, (num_cols, 1)).transpose()

        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        ss = stream.RGBStream("test", acd.content[0])
        pj = stream.RGBSpatialProjection(ss)
        time.sleep(0.5)

        # the maxzoom image has 2 tiles. So far 4 was read: 2 on the constructor, for
        # _updateHistogram and _updateDRange. And 2 for _updateImage, because .rect
        # and .mpp are initialized to the maxzoom image
        self.assertEqual(4, len(read_tiles))

        # delta full rect
        dfr = [-0.0015, -0.001, 0.0015, 0.001]
        full_image_rect = (POS[0] + dfr[0], POS[1] + dfr[1], POS[0] + dfr[2], POS[1] + dfr[3])

        # change both .rect and .mpp at the same time, to the same values
        # that are set on Stream constructor
        pj.rect.value = full_image_rect  # full image
        pj.mpp.value = pj.mpp.range[1]  # minimum zoom level

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.2)
        # no tiles are read from the disk
        self.assertEqual(4, len(read_tiles))
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 1)
        # top-left pixel of the left tile
        numpy.testing.assert_allclose([0, 0, 0], pj.image.value[0][0][0, 0, :])
        # top-right pixel of the left tile (which is little bit more half-way as the tile is 256px,
        # this covers more than the half the 375 px at minimum zoom -> 255*256/375 ~ 174, depending on the rounding)
        numpy.testing.assert_allclose([174, 0, 0], pj.image.value[0][0][0, 255, :], atol=1)
        # bottom-left pixel of the left tile
        numpy.testing.assert_allclose([0, 255, 0], pj.image.value[0][0][249, 0, :], atol=1)
        # bottom-right pixel of the right tile
        numpy.testing.assert_allclose([255, 255, 0], pj.image.value[1][0][249, 117, :], atol=1)

        # really small rect on the center, the tile is in the cache
        pj.rect.value = (POS[0], POS[1], POS[0] + 0.00001, POS[1] + 0.00001)

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        # no tiles are read from the disk
        self.assertEqual(4, len(read_tiles))
        self.assertEqual(len(pj.image.value), 1)
        self.assertEqual(len(pj.image.value[0]), 1)
        # top-left pixel of the only tile
        numpy.testing.assert_allclose([0, 0, 0], pj.image.value[0][0][0, 0, :], atol=1)
        # top-right pixel of the only tile
        numpy.testing.assert_allclose([174, 0, 0], pj.image.value[0][0][0, 255, :], atol=1)
        # bottom-left pixel of the only tile
        numpy.testing.assert_allclose([0, 255, 0], pj.image.value[0][0][249, 0, :], atol=1)

        # Now, just the tiny rect again, but at the minimum mpp (= fully zoomed in)
        # => should just need one new tile
        pj.mpp.value = pj.mpp.range[0]

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        # only one tile is read
        self.assertEqual(5, len(read_tiles))
        self.assertEqual(len(pj.image.value), 1)
        self.assertEqual(len(pj.image.value[0]), 1)
        # top-left pixel of the only tile
        numpy.testing.assert_allclose([108, 97, 0], pj.image.value[0][0][0, 0, :], atol=1)
        # top-right pixel of the only tile
        numpy.testing.assert_allclose([130, 97, 0], pj.image.value[0][0][0, 255, :], atol=1)
        # bottom-left pixel of the only tile
        numpy.testing.assert_allclose([108, 130, 0], pj.image.value[0][0][255, 0, :], atol=1)
        # bottom-right pixel of the only tile
        numpy.testing.assert_allclose([130, 130, 0], pj.image.value[0][0][255, 255, :], atol=1)

        # changing .rect and .mpp simultaneously
        # Note: the recommended way is to first change mpp and then rect, as it
        # ensures the first tiles read will not be at the wrong zoom level.
        # However, we do the opposite here, to check it doesn't go too wrong
        # (ie, first load the entire image at min mpp, and then load again at
        # max mpp). It should at worse have loaded one tile at the min mpp.
        pj.rect.value = full_image_rect  # full image
        # time.sleep(0.0001) # uncomment to test with slight delay between VA changes
        pj.mpp.value = pj.mpp.range[1]  # maximum zoom level

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        # Only 2 tiles read from disk. It means that the loop inside _updateImage,
        # triggered by the change on .rect was immediately stopped when .mpp changed
        if len(read_tiles) == 8:
            logging.warning("Two tiles read while expected to have just one, but "
                            "this is acceptable as updateImage thread might have "
                            "gone very fast.")
        else:
            self.assertEqual(7, len(read_tiles))
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 1)

        # top-left pixel of the left tile
        numpy.testing.assert_allclose([0, 0, 0], pj.image.value[0][0][0, 0, :], atol=1)
        # bottom-right pixel of the left tile
        numpy.testing.assert_allclose([174, 0, 0], pj.image.value[0][0][0, 255, :], atol=1)
        # bottom-right pixel of right tile
        numpy.testing.assert_allclose([255, 255, 0], pj.image.value[1][0][249, 117, :], atol=1)

        read_tiles = []  # reset, to keep the numbers simple

        delta = [d / 2 for d in dfr]
        # this rect is half the size of the full image, in the center of the image
        rect = (POS[0] + delta[0], POS[1] + delta[1],
                POS[0] + delta[2], POS[1] + delta[3])
        # changes .rect and .mpp simultaneously, simulating a GUI zoom
        pj.rect.value = rect
        # zoom 2
        pj.mpp.value = 4e-6
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.3)

        # reads 6 tiles from the disk, no tile is cached because the zoom changed
        self.assertEqual(6, len(read_tiles))
        self.assertEqual(len(pj.image.value), 3)
        self.assertEqual(len(pj.image.value[0]), 2)
        # top-left pixel of a center tile
        numpy.testing.assert_allclose([88, 0, 0], pj.image.value[1][0][0, 0, :], atol=1)
        # top-right pixel of a center tile
        numpy.testing.assert_allclose([174, 0, 0], pj.image.value[1][0][0, 255, :], atol=1)
        # bottom-left pixel of a center tile
        numpy.testing.assert_allclose([88, 130, 0], pj.image.value[1][0][255, 0, :], atol=1)
        # bottom pixel of a center tile
        numpy.testing.assert_allclose([174, 130, 0], pj.image.value[1][0][255, 255, :], atol=1)

        delta = [d / 8 for d in dfr]
        # this rect is 1/8 the size of the full image, in the center of the image
        rect = (POS[0] + delta[0], POS[1] + delta[1],
                POS[0] + delta[2], POS[1] + delta[3])
        # changes .rect and .mpp simultaneously, simulating a GUI zoom
        pj.rect.value = rect
        # zoom 0
        pj.mpp.value = pj.mpp.range[0]
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)

        # reads 4 tiles from the disk, no tile is cached because the zoom changed
        self.assertEqual(10, len(read_tiles))
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 2)
        # top-left pixel of the top-left tile
        numpy.testing.assert_allclose([108, 97, 0], pj.image.value[0][0][0, 0, :], atol=1)
        # top-right pixel of top-left tile
        numpy.testing.assert_allclose([130, 97, 0], pj.image.value[0][0][0, 255, :], atol=1)
        # bottom-left pixel of top-left tile
        numpy.testing.assert_allclose([108, 130, 0], pj.image.value[0][0][255, 0, :], atol=1)
        # bottom pixel of top-left tile
        numpy.testing.assert_allclose([130, 130, 0], pj.image.value[0][0][255, 255, :], atol=1)

        # get the old function back to the class
        tiff.DataArrayShadowPyramidalTIFF.getTile = tiff.DataArrayShadowPyramidalTIFF._getTileOldSZ

    def test_rgb_updatable_stream(self):
        """Test RGBUpdatableStream """

        # Test update function
        md = {
            model.MD_DESCRIPTION: "green dye",
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
            model.MD_POS: (13.7e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1,  # s
            model.MD_IN_WL: (600e-9, 620e-9),  # m
            model.MD_OUT_WL: (620e-9, 650e-9),  # m
            model.MD_USER_TINT: (0, 0, 255),  # RGB (blue)
            model.MD_ROTATION: 0.1,  # rad
            model.MD_SHEAR: 0,
            model.MD_DIMS: "YXC"
        }

        # Initial raw data
        da = model.DataArray(numpy.zeros((512, 1024, 3), dtype=numpy.uint8), md)
        strUpd = stream.RGBUpdatableStream("Test stream", da)
        numpy.testing.assert_array_equal(da, strUpd.raw[0])

        # Update with RGB
        new_da = model.DataArray(numpy.ones((512, 1024, 3), dtype=numpy.uint8), md)
        strUpd.update(new_da)
        numpy.testing.assert_array_equal(new_da, strUpd.raw[0])

        # Update with RGBA
        new_da = model.DataArray(numpy.ones((512, 1024, 4), dtype=numpy.uint8), md)
        strUpd.update(new_da)
        numpy.testing.assert_array_equal(new_da, strUpd.raw[0])

        # Pass wrong data shape and check if ValueError is raised
        new_da = model.DataArray(numpy.ones((512, 1024, 2), dtype=numpy.uint8), md)
        self.assertRaises(ValueError, strUpd.update, new_da)

        md[model.MD_DIMS] = "YXCT"
        new_da = model.DataArray(numpy.ones((512, 1024, 3, 3), dtype=numpy.uint8), md)
        self.assertRaises(ValueError, strUpd.update, new_da)

    def test_static_2d_updatable_stream(self):
        """Test Static2DUpdatableStream """
        md = {
            model.MD_BPP: 16,
            model.MD_BINNING: (2, 2),  # px, px
            model.MD_PIXEL_SIZE: (1e-4, 1e-4),  # m/px
            model.MD_EXP_TIME: 1,  # s
            model.MD_IN_WL: (0, 0),  # m
            model.MD_OUT_WL: 'pass-through',  # m
            model.MD_USER_TINT: (0, 0, 255),  # RGB (blue)
            model.MD_ROTATION: 0.1,  # rad
            model.MD_SHEAR: 0,
        }
        str_updatable = stream.Static2DUpdatableStream("Test stream", None)
        self.assertEqual(str_updatable.raw, [])
        da = model.DataArray(numpy.zeros((256, 256), dtype=numpy.uint8), md)
        str_updatable.update(da)
        self.assertEqual(str_updatable.tint.value, (0, 0, 255))
        str_updatable.tint.value = (255, 0, 0)
        self.assertEqual(str_updatable.tint.value, (255, 0, 0))
        self.assertEqual(str_updatable.raw[0].shape, (256, 256))

    def test_pixel_coordinates(self):
        """Test getPixelCoordinates and getRawValue"""
        md = {
            model.MD_DESCRIPTION: "green dye",
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
            model.MD_POS: (1e-3, -30e-3),  # m
            model.MD_ROTATION: 0,  # rad
            model.MD_SHEAR: 0,
            model.MD_EXP_TIME: 1,  # s
            model.MD_IN_WL: (600e-9, 620e-9),  # m
            model.MD_OUT_WL: (620e-9, 650e-9),  # m
            model.MD_USER_TINT: (0, 0, 255),  # RGB (blue)
        }

        # Image with even numbers on Y, and odd numbers on X, to test different cases
        res = (1025, 512)
        da = model.DataArray(numpy.zeros(res[::-1], dtype=numpy.uint16), md)
        da[12] = 2 ** 11  # whole line
        da[15] = 2 ** 10  # whole line

        fls = stream.StaticFluoStream(md[model.MD_DESCRIPTION], da)
        pj = stream.RGBSpatialProjection(fls)

        # getRawValue() is straightforward to test
        self.assertEqual(fls.getRawValue((0, 0)), 0)
        self.assertEqual(fls.getRawValue((4, 12)), 2 ** 11)
        self.assertEqual(fls.getRawValue((100, 15)), 2 ** 10)

        # Compute the coordinates of the center pixel, slightly to the left (as on Y, the
        # center is precisely between 2 pixels)
        center = md[model.MD_POS]
        pxs = md[model.MD_PIXEL_SIZE]
        center_top_phys = (center[0], center[1] + pxs[1] / 2)  # X, Y
        center_top_px = ((res[0] - 1) // 2, res[1] // 2 - 1)  # X, Y
        self.assertEqual(fls.getPixelCoordinates(center_top_phys), center_top_px)
        self.assertEqual(pj.getPixelCoordinates(center_top_phys), center_top_px)

        # Center of the top-left pixel
        left_top_phys = (center[0] - ((res[0] - 1) / 2) * pxs[0],
                         center[1] + ((res[1] - 1) / 2) * pxs[1])
        left_top_px = (0, 0)
        self.assertEqual(fls.getPixelCoordinates(left_top_phys), left_top_px)
        self.assertEqual(pj.getPixelCoordinates(left_top_phys), left_top_px)

        # Just slightly outside the top-left pixel => should return None
        out_x_phys = left_top_phys[0] - pxs[0], left_top_phys[1]
        self.assertEqual(fls.getPixelCoordinates(out_x_phys), None)
        self.assertEqual(pj.getPixelCoordinates(out_x_phys), None)

        out_y_phys = left_top_phys[0] - pxs[0], left_top_phys[1]
        self.assertEqual(fls.getPixelCoordinates(out_y_phys), None)
        self.assertEqual(pj.getPixelCoordinates(out_y_phys), None)


if __name__ == '__main__':
    unittest.main()
