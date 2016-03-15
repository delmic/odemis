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
from odemis import model
from odemis.gui.util import img
from odemis.gui.util.img import wxImage2NDImage
from odemis.util.polar import THETA_SIZE, PHI_SIZE
import unittest
import wx


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
        self.assertTrue((ndimage[0,0] == [0, 0, 0]).all())

    # TODO alpha channel


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
        raw_polar = img.calculate_raw_polar(data, data)
        self.assertEqual(raw_polar.shape, (THETA_SIZE + 1, PHI_SIZE + 1))  # plus theta/phi values


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

    def test_simple(self):
        pass

if __name__ == "__main__":
    unittest.main()

