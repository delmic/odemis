# -*- coding: utf-8 -*-
'''
Created on 19 Sep 2012

@author: piel

Copyright © 2012-2013 Éric Piel & Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import cairo
import logging
import math
import numpy
import os
import time
import unittest
import wx

from odemis import model, dataio
from odemis.acq import stream
from odemis.gui.util import img
from odemis.gui.util.img import wxImage2NDImage, format_rgba_darray


logging.getLogger().setLevel(logging.DEBUG)


def GetRGB(im, x, y):
    """
    return the r,g,b tuple corresponding to a pixel
    """
    r = im.GetRed(x, y)
    g = im.GetGreen(x, y)
    b = im.GetBlue(x, y)

    return (r, g, b)


class TestWxImage2NDImage(unittest.TestCase):

    def test_simple(self):
        size = (32, 64)
        wximage = wx.EmptyImage(*size) # black RGB
        ndimage = wxImage2NDImage(wximage)
        self.assertEqual(ndimage.shape[0:2], size[-1:-3:-1])
        self.assertEqual(ndimage.shape[2], 3) # RGB
        self.assertTrue((ndimage[0, 0] == [0, 0, 0]).all())

    # TODO alpha channel


class TestRGBA(unittest.TestCase):

    def test_rgb_to_bgra(self):
        size = (32, 64, 3)
        rgbim = model.DataArray(numpy.zeros(size, dtype=numpy.uint8))
        rgbim[:, :, 0] = 1
        rgbim[:, :, 1] = 100
        rgbim[:, :, 2] = 200
        bgraim = format_rgba_darray(rgbim, 255)

        # Checks it added alpha channel
        self.assertEqual(bgraim.shape, (32, 64, 4))
        self.assertEqual(bgraim[0, 0, 3], 255)
        # Check the channels were swapped to BGR
        self.assertTrue((bgraim[1, 1] == [200, 100, 1, 255]).all())

    def test_rgb_alpha_to_bgra(self):
        size = (32, 64, 3)
        rgbim = model.DataArray(numpy.zeros(size, dtype=numpy.uint8))
        rgbim[:, :, 0] = 1
        rgbim[:, :, 1] = 100
        rgbim[:, :, 2] = 200
        bgraim = format_rgba_darray(rgbim, 0)

        # Checks it added alpha channel and set everything to scale
        self.assertEqual(bgraim.shape, (32, 64, 4))
        self.assertTrue((bgraim == 0).all())

    def test_rgba_to_bgra(self):
        size = (32, 64, 4)
        rgbaim = model.DataArray(numpy.zeros(size, dtype=numpy.uint8))
        rgbaim[:, :, 0] = 1
        rgbaim[:, :, 1] = 100
        rgbaim[:, :, 2] = 200
        rgbaim[:, :, 3] = 255
        rgbaim[2, 2, 3] = 0
        bgraim = format_rgba_darray(rgbaim)

        # Checks it added alpha channel
        self.assertEqual(bgraim.shape, (32, 64, 4))
        # Check the channels were swapped to BGR
        self.assertTrue((bgraim[1, 1] == [200, 100, 1, 255]).all())
        self.assertTrue((bgraim[2, 2] == [200, 100, 1, 0]).all())


class TestARExport(unittest.TestCase):

    def test_ar_frame(self):
        ar_margin = 100
        img_size = 512, 512
        ar_size = img_size[0] + ar_margin, img_size[1] + ar_margin
        data_to_draw = numpy.zeros((ar_size[1], ar_size[0], 4), dtype=numpy.uint8)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, ar_size[0], ar_size[1])
        ctx = cairo.Context(surface)
        app = wx.App()  # needed for the gui font name
        font_name = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT).GetFaceName()
        tau = 2 * math.pi
        ticksize = 10
        num_ticks = 6
        ticks_info = img.ar_create_tick_labels(img_size, ticksize, num_ticks, tau, ar_margin / 2)
        ticks, (center_x, center_y), inner_radius, radius = ticks_info
        # circle expected just on the center of the frame
        self.assertEqual((center_x, center_y), (ar_size[0] / 2, ar_size[1] / 2))
        self.assertLess(radius, ar_size[0] / 2)  # circle radius within limits
        img.draw_ar_frame(ctx, ar_size, ticks, font_name, center_x, center_y, inner_radius, radius, tau)
        self.assertEqual(data_to_draw.shape[:2], ar_size)  # frame includes the margin

    def test_ar_raw(self):
        data = model.DataArray(numpy.zeros((256, 256)), metadata={model.MD_AR_POLE: (100, 100),
                                                                  model.MD_PIXEL_SIZE: (1e-03, 1e-03)})
        raw_polar = img.calculate_raw_ar(data, data)
        # shape = raw data + theta/phi axes values
        self.assertGreater(raw_polar.shape[0], 50)
        self.assertGreater(raw_polar.shape[1], 50)

    def test_ar_export(self):

        filename = "test-ar.csv"

        # Create AR data
        md = {
            model.MD_SW_VERSION: "1.0-test",
            model.MD_HW_NAME: "fake ccd",
            model.MD_DESCRIPTION: "AR",
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

        md0 = dict(md)
        data0 = model.DataArray(1500 + numpy.zeros((512, 512), dtype=numpy.uint16), md0)
        md1 = dict(md)
        md1[model.MD_POS] = (1.5e-3, -30e-3)
        md1[model.MD_BASELINE] = 300  # AR background should take this into account
        data1 = model.DataArray(3345 + numpy.zeros((512, 512), dtype=numpy.uint16), md1)

        # Create AR stream
        ars = stream.StaticARStream("test", [data0, data1])
        ars.point.value = md1[model.MD_POS]

        # Convert to exportable RGB image
        exdata = img.ar_to_export_data([ars], raw=True)
        # shape = raw data + theta/phi axes values
        self.assertGreater(exdata.shape[0], 50)
        self.assertGreater(exdata.shape[1], 50)

        # Save into a CSV file
        exporter = dataio.get_converter("CSV")
        exporter.export(filename, exdata)
        st = os.stat(filename)  # this test also that the file is created
        self.assertGreater(st.st_size, 100)

        # clean up
        try:
            os.remove(filename)
        except Exception:
            pass

    # TODO: check that exporting large AR image doesn't get crazy memory usage

    def test_big_ar_export(self):

        filename = "test-ar.csv"

        # Create AR data
        md = {
            model.MD_SW_VERSION: "1.0-test",
            model.MD_HW_NAME: "fake ccd",
            model.MD_DESCRIPTION: "AR",
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

        md0 = dict(md)
        data0 = model.DataArray(1500 + numpy.zeros((1080, 1024), dtype=numpy.uint16), md0)

        # Create AR stream
        ars = stream.StaticARStream("test", [data0])
        ars.point.value = md0[model.MD_POS]

        # Convert to exportable RGB image
        exdata = img.ar_to_export_data([ars], raw=True)
        # shape = raw data + theta/phi axes values
        self.assertGreater(exdata.shape[0], 50)
        self.assertGreater(exdata.shape[1], 50)

        # Save into a CSV file
        exporter = dataio.get_converter("CSV")
        exporter.export(filename, exdata)
        st = os.stat(filename)  # this test also that the file is created
        self.assertGreater(st.st_size, 100)

        # clean up
        try:
            os.remove(filename)
        except Exception:
            pass


class TestSpectrumExport(unittest.TestCase):

    def setUp(self):
        self.spectrum = numpy.linspace(0, 750, 200)
        self.spectrum_range = numpy.linspace(4.7e-07, 1.02e-06, 200)
        self.unit = "m"
        self.app = wx.App()  # needed for the gui font name

    def test_spectrum_ready(self):
        exported_data = img.spectrum_to_export_data(self.spectrum, False, self.unit, self.spectrum_range)
        self.assertEqual(exported_data.metadata[model.MD_DIMS], 'YXC')  # ready for RGB export
        self.assertEqual(exported_data.shape[:2],
                         (img.SPEC_PLOT_SIZE + 2 * img.SPEC_SCALE_WIDTH,
                          img.SPEC_PLOT_SIZE + 2 * img.SPEC_SCALE_WIDTH))  # exported image includes scale bars

    def test_spectrum_raw(self):
        exported_data = img.spectrum_to_export_data(self.spectrum, True, self.unit, self.spectrum_range)
        self.assertEqual(exported_data.shape[0], len(self.spectrum_range))  # exported image includes only raw data


class TestSpatialExport(unittest.TestCase):

    def setUp(self):
        self.app = wx.App()
        data = numpy.zeros((2160, 2560), dtype=numpy.uint16)
        dataRGB = numpy.zeros((2160, 2560, 4))
        metadata = {'Hardware name': 'Andor ZYLA-5.5-USB3 (s/n: VSC-01959)',
                    'Exposure time': 0.3, 'Pixel size': (1.59604600574173e-07, 1.59604600574173e-07),
                    'Acquisition date': 1441361559.258568, 'Hardware version': "firmware: '14.9.16.0' (driver 3.10.30003.5)",
                    'Centre position': (-0.001203511795256, -0.000295338300158), 'Lens magnification': 40.0,
                    'Input wavelength range': (6.15e-07, 6.350000000000001e-07), 'Shear':-4.358492733391727e-16,
                    'Description': 'Filtered colour 1', 'Bits per pixel': 16, 'Binning': (1, 1), 'Pixel readout time': 1e-08,
                    'Gain': 1.1, 'Rotation': 6.279302551026012, 'Light power': 0.0, 'Display tint': (255, 0, 0),
                    'Output wavelength range': (6.990000000000001e-07, 7.01e-07)}
        image = model.DataArray(data, metadata)
        fluo_stream = stream.StaticFluoStream(metadata['Description'], image)
        fluo_stream.image.value = model.DataArray(dataRGB, metadata)
        data = numpy.zeros((1024, 1024), dtype=numpy.uint16)
        dataRGB = numpy.zeros((1024, 1024, 4))
        metadata = {'Hardware name': 'pcie-6251', 'Description': 'Secondary electrons',
                    'Exposure time': 3e-06, 'Pixel size': (5.9910982493639e-08, 6.0604642506361e-08),
                    'Acquisition date': 1441361562.0, 'Hardware version': 'Unknown (driver 2.1-160-g17a59fb (driver ni_pcimio v0.7.76))',
                    'Centre position': (-0.001203511795256, -0.000295338300158), 'Lens magnification': 5000.0, 'Rotation': 0.0,
                    'Shear': 0.003274715695854}
        image = model.DataArray(data, metadata)
        sem_stream = stream.StaticSEMStream(metadata['Description'], image)
        sem_stream.image.value = model.DataArray(dataRGB, metadata)
        self.streams = [fluo_stream, sem_stream]
        self.min_res = (623, 432)

    def test_no_crop_need(self):
        """
        Data roi covers the whole window view
        """
        view_hfw = (0.00025158414075691866, 0.00017445320835792754)
        view_pos = [-0.001211588332679978, -0.00028726176273402186]
        draw_merge_ratio = 0.3
        exp_data = img.images_to_export_data([self.streams[0]], view_hfw, view_pos, draw_merge_ratio, False)
        self.assertEqual(exp_data[0].shape, (1226, 1576, 4))  # RGB

    def test_crop_need(self):
        """
        Data roi covers part of the window view thus we need to crop the
        intersection with the data
        """
        view_hfw = (0.0005031682815138373, 0.0003489064167158551)
        view_pos = [-0.001211588332679978, -0.00028726176273402186]
        draw_merge_ratio = 0.3
        exp_data = img.images_to_export_data([self.streams[0]], view_hfw, view_pos, draw_merge_ratio, False)
        self.assertEqual(exp_data[0].shape, (2340, 2560, 4))  # RGB

    def test_crop_and_interpolation_need(self):
        """
        Data roi covers part of the window view and data resolution is below
        the minimum limit thus we need to interpolate the data in order to
        keep the shape ratio unchanged
        """
        view_hfw = (0.0010063365630276746, 0.0006978128334317102)
        view_pos = [-0.0015823014004405739, -0.0008081984265806109]
        draw_merge_ratio = 0.3
        exp_data = img.images_to_export_data([self.streams[0]], view_hfw, view_pos, draw_merge_ratio, False)
        self.assertEqual(exp_data[0].shape, (182, 1673, 4))  # RGB

    def test_multiple_streams(self):
        # Print ready format
        view_hfw = (8.191282393266523e-05, 6.205915392651362e-05)
        view_pos = [-0.001203511795256, -0.000295338300158]
        draw_merge_ratio = 0.3
        exp_data = img.images_to_export_data(self.streams, view_hfw, view_pos, draw_merge_ratio, False)
        self.assertEqual(len(exp_data), 1)
        self.assertEqual(len(exp_data[0].shape), 3)  # RGB

        # Post-process format
        exp_data = img.images_to_export_data(self.streams, view_hfw, view_pos, draw_merge_ratio, True)
        self.assertEqual(len(exp_data), 2)
        self.assertEqual(len(exp_data[0].shape), 2)  # grayscale
        self.assertEqual(len(exp_data[1].shape), 2)  # grayscale
        self.assertEqual(exp_data[0].shape, exp_data[1].shape)  # all exported images must have the same shape

    def test_no_intersection(self):
        """
        Data has no intersection with the window view
        """
        view_hfw = (0.0001039324002586505, 6.205915392651362e-05)
        view_pos = [-0.00147293527265202, -0.0004728408264424368]
        draw_merge_ratio = 0.3
        with self.assertRaises(LookupError):
            img.images_to_export_data(self.streams, view_hfw, view_pos, draw_merge_ratio, False)

    def test_thin_column(self):
        """
        Test that minimum width limit is fulfilled in case only a very thin
        column of data is in the view
        """
        view_hfw = (8.191282393266523e-05, 6.205915392651362e-05)
        view_pos = [-0.0014443006338779269, -0.0002968821446105185]
        draw_merge_ratio = 0.3
        exp_data = img.images_to_export_data(self.streams, view_hfw, view_pos, draw_merge_ratio, False)
        self.assertEqual(len(exp_data), 1)
        self.assertEqual(len(exp_data[0].shape), 3)  # RGB
        self.assertEqual(exp_data[0].shape[1], img.CROP_RES_LIMIT)


if __name__ == "__main__":
    unittest.main()

