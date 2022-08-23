# -*- coding: utf-8 -*-
'''
Created on 3 Mar 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
import numpy
import math
from odemis import model, dataio
from odemis.acq import calibration
from odemis.acq.stream import POL_POSITIONS
from odemis.util import img
import os
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)


class TestAR(unittest.TestCase):
    """
    Test the AR related functions
    """

    def test_load_simple(self):
        # AR background data
        dcalib = numpy.zeros((512, 1024), dtype=numpy.uint16)
        md = {model.MD_SW_VERSION: "1.0-test",
             model.MD_HW_NAME: "fake ccd",
             model.MD_DESCRIPTION: "AR",
             model.MD_ACQ_DATE: time.time(),
             model.MD_BPP: 12,
             model.MD_BINNING: (1, 1), # px, px
             model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6), # m/px
             model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
             model.MD_POS: (1.2e-3, -30e-3), # m
             model.MD_EXP_TIME: 1.2, # s
             model.MD_AR_POLE: (253.1, 65.1),
             model.MD_LENS_MAG: 60, # ratio
            }
        calib = model.DataArray(dcalib, md)

        # Give one DA, the correct one, so expect to get it back
        out = calibration.get_ar_data([calib])
        numpy.testing.assert_equal(out, calib)

        # More DataArrays, just to make it slightly harder to find the data
        data1 = model.DataArray(numpy.ones((1, 1, 1, 520, 230), dtype=numpy.uint16),
                                metadata={model.MD_POS: (1.2e-3, -30e-3)})
        data2 = model.DataArray(17 * numpy.ones((1, 1), dtype=numpy.uint16),
                                metadata={model.MD_POS: (1.2e-3, -30e-3)})
        out = calibration.get_ar_data([data1, calib, data2])
        numpy.testing.assert_equal(out, calib)

    def test_load_multi(self):
        # AR background data
        dcalib = numpy.zeros((512, 1024), dtype=numpy.uint16)
        md = {model.MD_SW_VERSION: "1.0-test",
             model.MD_HW_NAME: "fake ccd",
             model.MD_DESCRIPTION: "AR",
             model.MD_ACQ_DATE: time.time(),
             model.MD_BPP: 12,
             model.MD_BINNING: (1, 1),  # px, px
             model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6),  # m/px
             model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
             model.MD_POS: (1.2e-3, -30e-3),  # m
             model.MD_EXP_TIME: 1.2,  # s
             model.MD_AR_POLE: (253.1, 65.1),
             model.MD_LENS_MAG: 60,  # ratio
            }
        calib = model.DataArray(dcalib, md)
        calib2 = model.DataArray(dcalib + 1, md)

        # Give one DA, the correct one, so expect to get it back
        out = calibration.get_ar_data([calib, calib2])
        # The average (of calib=0 and calib2=1)
        numpy.testing.assert_equal(out.shape, calib.shape)
        numpy.testing.assert_equal(out, 0.5)
        self.assertIsInstance(out, model.DataArray)
        self.assertEqual(out.metadata[model.MD_AR_POLE], md[model.MD_AR_POLE])

        # More DataArrays, just to make it slightly harder to find the data
        data1 = model.DataArray(numpy.ones((1, 1, 1, 520, 230), dtype=numpy.uint16),
                                metadata={model.MD_POS: (1.2e-3, -30e-3)})
        data2 = model.DataArray(17 * numpy.ones((1, 1), dtype=numpy.uint16),
                                metadata={model.MD_POS: (1.2e-3, -30e-3)})
        out = calibration.get_ar_data([data1, calib2, data2, calib])
        numpy.testing.assert_equal(out.shape, calib.shape)
        numpy.testing.assert_equal(out, 0.5)
        self.assertIsInstance(out, model.DataArray)

    def test_load_full(self):
        """
        Check the whole sequence: saving calibration data to file, loading it
        back from file, finding it.
        """
        # AR background data
        dcalib = numpy.zeros((512, 1024), dtype=numpy.uint16)
        md = {model.MD_SW_VERSION: "1.0-test",
             model.MD_HW_NAME: "fake ccd",
             model.MD_DESCRIPTION: "AR",
             model.MD_ACQ_DATE: time.time(),
             model.MD_BPP: 12,
             model.MD_BINNING: (1, 1), # px, px
             model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6), # m/px
             model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
             model.MD_POS: (1.2e-3, -30e-3), # m
             model.MD_EXP_TIME: 1.2, # s
             model.MD_AR_POLE: (253.1, 65.1),
             model.MD_LENS_MAG: 60, # ratio
            }
        calib = model.DataArray(dcalib, md)

        # Give one DA, the correct one, so expect to get it back
        out = calibration.get_ar_data([calib])
        numpy.testing.assert_equal(out, calib)

        # More DataArrays, just to make it slightly harder to find the data
        data1 = model.DataArray(numpy.ones((1, 1, 1, 520, 230), dtype=numpy.uint16),
                                metadata={model.MD_POS: (1.2e-3, -30e-3)})
        data2 = model.DataArray(17 * numpy.ones((1, 1), dtype=numpy.uint16),
                                metadata={model.MD_POS: (1.2e-3, -30e-3)})
        # RGB image
        thumb = model.DataArray(numpy.ones((520, 230, 3), dtype=numpy.uint8))

        full_data = [data1, calib, data2]

        for fmt in dataio.get_available_formats(os.O_WRONLY):
            exporter = dataio.get_converter(fmt)
            logging.info("Trying to export/import with %s", fmt)
            fn = u"test_ar" + exporter.EXTENSIONS[0]
            exporter.export(fn, full_data, thumb)

            if fmt in dataio.get_available_formats(os.O_RDONLY):
                idata = exporter.read_data(fn)
                icalib = calibration.get_ar_data(idata)
                icalib2d = img.ensure2DImage(icalib)
                numpy.testing.assert_equal(icalib2d, calib)
                numpy.testing.assert_almost_equal(icalib.metadata[model.MD_AR_POLE],
                                                  calib.metadata[model.MD_AR_POLE])
            try:
                os.remove(fn)
            except OSError:
                logging.exception("Failed to delete the file %s", fn)

    def test_load_simple_arpol(self):
        # AR polarimetry background data

        metadata = []
        pol_positions = list(POL_POSITIONS)
        qwp_positions = [0.0, 1.570796, 0.785398, 2.356194, 0.0, 0.0]
        linpol_positions = [0.0, 1.570796, 0.785398, 2.356194, 0.785398, 2.356194]

        # ARPOL metadata
        for idx in range(len(pol_positions)):
            metadata.append({model.MD_SW_VERSION: "1.0-test",
                             model.MD_HW_NAME: "fake ccd",
                             model.MD_DESCRIPTION: "ARPOL",
                             model.MD_ACQ_DATE: time.time(),
                             model.MD_BPP: 12,
                             model.MD_BINNING: (1, 1),  # px, px
                             model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6),  # m/px
                             model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
                             model.MD_POS: (1.2e-3, -30e-3),  # m
                             model.MD_EXP_TIME: 1.2,  # s
                             model.MD_AR_POLE: (253.1, 65.1),
                             model.MD_LENS_MAG: 0.4,  # ratio
                             model.MD_POL_MODE: pol_positions[idx],
                             model.MD_POL_POS_LINPOL: qwp_positions[idx],  # rad
                             model.MD_POL_POS_QWP: linpol_positions[idx],  # rad
                             })

        bg_data = []  # list of background images
        for idx in range(len(pol_positions)):
            dcalib = numpy.ones((1, 1, 1, 512, 1024), dtype=numpy.uint16) + idx  # different bg image for each pol
            calib = model.DataArray(dcalib, metadata[idx])
            bg_data.append(calib)

        # Give one DA, the correct one, so expect to get it back
        out = calibration.get_ar_data(bg_data)

        # Check the expected and return lists contain the same DataArrays
        self.assertEqual(len(bg_data), len(out))
        for da in out:
            self.assertTrue(any(numpy.array_equal(da, bg) for bg in bg_data))

        # More DataArrays, just to make it slightly harder to find the data
        data1 = model.DataArray(numpy.ones((1, 1, 1, 520, 230), dtype=numpy.uint16),
                                metadata={model.MD_POS: (1.2e-3, -30e-3)})
        data2 = model.DataArray(17 * numpy.ones((1, 1), dtype=numpy.uint16),
                                metadata={model.MD_POS: (1.2e-3, -30e-3)})
        bg_data_new = [data1, data2] + bg_data
        out = calibration.get_ar_data(bg_data_new)

        # Check the expected and return lists contain the same DataArrays
        self.assertEqual(len(bg_data), len(out))
        for da in out:
            self.assertTrue(any(numpy.array_equal(da, bg) for bg in bg_data))


class TestSpectrum(unittest.TestCase):
    """
    Test the Spectrum related functions
    """


    def test_load_background(self):
        # Background data
        dcalib = numpy.array([1, 2, 2, 3, 4, 5, 4, 6, 9], dtype=numpy.uint16)
        dcalib.shape += (1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.arange(dcalib.shape[0]) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        # Give one DA, the correct one, so expect to get it back
        out = calibration.get_spectrum_data([calib])
        numpy.testing.assert_equal(out, calib)
        numpy.testing.assert_almost_equal(out.metadata[model.MD_WL_LIST],
                                          calib.metadata[model.MD_WL_LIST])

        # More DataArrays, just to make it slightly harder to find the data
        data1 = model.DataArray(numpy.ones((1, 1, 1, 520, 230), dtype=numpy.uint16))
        out = calibration.get_spectrum_data([data1, calib])
        numpy.testing.assert_equal(out, calib)
        numpy.testing.assert_almost_equal(out.metadata[model.MD_WL_LIST],
                                          calib.metadata[model.MD_WL_LIST])

        # should also load spectra with more than one points (then return just
        # the first point)
        dcalibxy = numpy.ones((128, 1, 1, 24, 25), dtype=numpy.uint8)
        dcalibxy[:, :, :, :] = numpy.arange(dcalibxy.shape[-1])
        dcalibxy[0, 0, 0, 0, 0] = 0
        wl_calibxy = 400e-9 + numpy.arange(dcalibxy.shape[0]) * 10e-9
        calibxy = model.DataArray(dcalibxy, metadata={model.MD_WL_LIST: wl_calibxy})
        out = calibration.get_spectrum_data([data1, calibxy])
        eout = calibxy[:, 0:1, 0:1, 0:1, (dcalibxy.shape[-1] - 1) // 2] # middle should contain average
        eout.shape += (1,)
        numpy.testing.assert_equal(out, eout)
        numpy.testing.assert_almost_equal(out.metadata[model.MD_WL_LIST],
                                          calibxy.metadata[model.MD_WL_LIST])

    def test_load_compensation(self):
        # Compensation data
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 0.1, 6, 9.1], dtype=numpy.float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.arange(dcalib.shape[0]) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        # Give one DA, the correct one, so expect to get it back
        out = calibration.get_spectrum_efficiency([calib])
        numpy.testing.assert_equal(out, calib)
        numpy.testing.assert_almost_equal(out.metadata[model.MD_WL_LIST],
                                          calib.metadata[model.MD_WL_LIST])

        # More DataArrays, just to make it slightly harder to find the data
        data1 = model.DataArray(numpy.ones((1, 1, 1, 520, 230), dtype=numpy.uint16))
        out = calibration.get_spectrum_efficiency([data1, calib])
        numpy.testing.assert_equal(out, calib)
        numpy.testing.assert_almost_equal(out.metadata[model.MD_WL_LIST],
                                          calib.metadata[model.MD_WL_LIST])

    def test_load_full(self):
        """
        Check the whole sequence: saving calibration data to file, loading it
        back from file, finding it.
        """
        # Background data
        dbckg = numpy.array([1, 2, 2, 3, 4, 5, 4, 6, 9], dtype=numpy.uint16)
        dbckg.shape += (1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.arange(dbckg.shape[0]) * 10e-9
        bckg = model.DataArray(dbckg, metadata={model.MD_WL_LIST: wl_calib})

        # Give one DA, the correct one, so expect to get it back

        # Compensation data
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 0.1, 6, 9.1], dtype=numpy.float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.arange(dcalib.shape[0]) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        # More DataArrays, just to make it slightly harder to find the data
        data1 = model.DataArray(numpy.ones((1, 1, 1, 520, 230), dtype=numpy.uint16))
        data2 = model.DataArray(numpy.zeros((3, 1, 1, 520, 230), dtype=numpy.uint16))

        # RGB image
        thumb = model.DataArray(numpy.ones((520, 230, 3), dtype=numpy.uint8))

        full_coef = [data1, calib, data2]
        full_bckg = [data1, bckg, data2]

        for fmt in dataio.get_available_formats(os.O_WRONLY):
            exporter = dataio.get_converter(fmt)
            logging.info("Trying to export/import with %s", fmt)
            fn_coef = u"test_spec" + exporter.EXTENSIONS[0]
            exporter.export(fn_coef, full_coef, thumb)
            fn_bckg = u"test_bckg" + exporter.EXTENSIONS[0]
            exporter.export(fn_bckg, full_bckg, thumb)

            if fmt in dataio.get_available_formats(os.O_RDONLY):
                data_bckg = exporter.read_data(fn_bckg)
                ibckg = calibration.get_spectrum_data(data_bckg)
                data_coef = exporter.read_data(fn_coef)
                icoef = calibration.get_spectrum_efficiency(data_coef)
                numpy.testing.assert_equal(icoef, calib)
                numpy.testing.assert_almost_equal(icoef.metadata[model.MD_WL_LIST],
                                                  calib.metadata[model.MD_WL_LIST])
                numpy.testing.assert_equal(ibckg, bckg)
                numpy.testing.assert_almost_equal(ibckg.metadata[model.MD_WL_LIST],
                                                  bckg.metadata[model.MD_WL_LIST])
            try:
                os.remove(fn_coef)
            except OSError:
                logging.exception("Failed to delete the file %s", fn_coef)
            try:
                os.remove(fn_bckg)
            except OSError:
                logging.exception("Failed to delete the file %s", fn_bckg)

    def test_compensate(self):
        """Test applying efficiency compensation"""
        # Spectrum
        data = numpy.ones((251, 1, 1, 200, 300), dtype="uint16") + 1
        wld = 433e-9 + numpy.arange(data.shape[0]) * 0.1e-9
        spec = model.DataArray(data, metadata={model.MD_WL_LIST: wld})

        # Background data
        dbckg = numpy.ones(data.shape, dtype=numpy.uint16)
        wl_bckg = 400e-9 + numpy.arange(dbckg.shape[0]) * 10e-9
        obckg = model.DataArray(dbckg, metadata={model.MD_WL_LIST: wl_bckg})
        bckg = calibration.get_spectrum_data([obckg])

        # Compensation data
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 0.1, 6, 9.1], dtype=numpy.float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.arange(dcalib.shape[0]) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        compensated = calibration.apply_spectrum_corrections(spec, bckg, calib)

        self.assertEqual(spec.shape, compensated.shape)
        numpy.testing.assert_equal(spec.metadata[model.MD_WL_LIST],
                                   compensated.metadata[model.MD_WL_LIST])

        for i in range(dcalib.shape[0] - 1):
            ca, cb = calib[i], calib[i + 1]
            wla, wlb = wl_calib[i], wl_calib[i + 1]
            # All the values between the 2 wavelengths should be compensated
            # between the 2 factors

            for vo, vb, vc, wl in zip(spec[..., 3, 3], bckg[..., 0, 0], compensated[..., 3, 3], wld):
                if wla <= wl <= wlb:
                    expa, expb = (vo - vb) * ca, (vo - vb) * cb
                    minc, maxc = min(expa, expb), max(expa, expb)
                    self.assertTrue(minc <= vc <= maxc)

    def test_compensate_out(self):
        """Test applying efficiency compensation on an edge of calibration"""
        # Spectrum
        data = numpy.ones((251, 1, 1, 200, 300), dtype="uint16")
        wld = 333e-9 + numpy.arange(data.shape[0]) * 0.1e-9
        spec = model.DataArray(data, metadata={model.MD_WL_LIST: wld})

        # Only from 400 nm => need to use the border (=1) for everything below
        dcalib = numpy.array([1, 1, 2, 3, 4, 5, 1, 6, 9], dtype=numpy.float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.arange(dcalib.shape[0]) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        compensated = calibration.apply_spectrum_corrections(spec, coef=calib)

        self.assertEqual(spec.shape, compensated.shape)
        numpy.testing.assert_equal(spec.metadata[model.MD_WL_LIST],
                                   compensated.metadata[model.MD_WL_LIST])

        # Value before the first calibration wavelength must be estimated
        for vo, vc, wl in zip(spec[..., 3, 3], compensated[..., 3, 3], wld):
            if wl <= wl_calib[0]:
                self.assertEqual(vo * dcalib[0], vc)

    def test_theta_list_nan(self):
        """Check that all the NaNs of MD_THETA_LIST are removed"""
        # AR Spectrum (aka EK1) data
        data = numpy.ones((256, 128, 1, 2, 3), dtype="uint16")
        wld = numpy.linspace(333e-9, 511e-9, data.shape[0])
        angles = numpy.linspace(-1.1, 1.5, data.shape[1])
        # set a few angles as NaN, and mark them in the data with a different value
        for i in [0, 1, -1]:
            angles[i] = math.nan
            data[:, i,:,:,:] = 2

        md = {model.MD_WL_LIST: wld.tolist(),
              model.MD_THETA_LIST: angles.tolist(),
              model.MD_DIMS: "CAZYX",
        }
        arspec = model.DataArray(data, md)
        orig_arspec = data.copy()
        orig_md = md.copy()

        calibrated = calibration.apply_spectrum_corrections(arspec)

        # NaNs should be gone
        angles_cal = calibrated.metadata[model.MD_THETA_LIST]
        self.assertEqual(len(angles_cal), len(angles) - 3)
        self.assertFalse(any(math.isnan(x) for x in angles_cal))
        self.assertEqual(calibrated.shape, (256, 128 - 3 , 1, 2, 3))
        numpy.testing.assert_array_equal(calibrated, 1)
        
        # The original data shouldn't have been changed
        numpy.testing.assert_array_equal(arspec, orig_arspec)
        self.assertEqual(arspec.metadata, orig_md)


TIME_RANGE_TO_DELAY_EX = {1e-09: 7.99e-09,
                          2e-09: 9.63e-09,
                          5e-09: 3.32e-08,
                          1e-08: 4.59e-08,
                          2e-08: 6.64e-08,
                          5e-08: 1.02e-07,
                          1e-07: 1.69e-07,
                          2e-07: 3.02e-07,
                          5e-07: 7.31e-07,
                          0.0001: 0.000161,
                          0.0002: 0.00032,
                          0.0005: 0.000798,
                          0.01: 0.0154}


class TriggerDelayTest(unittest.TestCase):
    """
    test the functions related to writing & reading MD_TIME_RANGE_TO_DELAY
    """

    def test_write_read_trigger_delays(self):
        TRIG_DELAY_FILE = "test-trig-delays.csv"

        # Make sure the file is not present
        try:
            os.remove(TRIG_DELAY_FILE)
        except Exception:
            pass # no such file

        # Write the file
        calibration.write_trigger_delay_csv(TRIG_DELAY_FILE, TIME_RANGE_TO_DELAY_EX)

        # Read back
        tr2d = calibration.read_trigger_delay_csv(TRIG_DELAY_FILE,
                                                  set(TIME_RANGE_TO_DELAY_EX.keys()),
                                                  [0, 0.1])
        self.assertEqual(TIME_RANGE_TO_DELAY_EX, tr2d)

        # Read with wrong hardware
        with self.assertRaises(ValueError):
            tr2d = calibration.read_trigger_delay_csv(TRIG_DELAY_FILE,
                                                      {1e-09, 7e-9},  # Wrong time ranges
                                                      [0, 0.1])
        with self.assertRaises(ValueError):
            tr2d = calibration.read_trigger_delay_csv(TRIG_DELAY_FILE,
                                                      set(TIME_RANGE_TO_DELAY_EX.keys()),
                                                      [0, 1e-6])  # Out of delay range

        # Try overwriting
        calibration.write_trigger_delay_csv(TRIG_DELAY_FILE, TIME_RANGE_TO_DELAY_EX)

        os.remove(TRIG_DELAY_FILE)


if __name__ == "__main__":
    unittest.main()
