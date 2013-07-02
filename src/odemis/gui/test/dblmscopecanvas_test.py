#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 10 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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

from odemis import model
from odemis.gui import instrmodel, test
from odemis.gui.dblmscopecanvas import DblMicroscopeCanvas
from odemis.gui.model.img import InstrumentalImage
from odemis.gui.model.stream import StaticStream
import logging
import unittest
import wx

logging.getLogger().setLevel(logging.DEBUG)

class FakeMicroscopeGUI(object):
    """
    Imitates a MicroscopeModel wrt stream entry: it just needs a focussedView
    """
    def __init__(self):
        fview = instrmodel.MicroscopeView("fakeview")
        self.focussedView = model.VigilantAttribute(fview)

class TestDblMicroscopeCanvas(unittest.TestCase):

    def setUp(self):
        self.app = wx.PySimpleApp()
        self.frame = wx.Frame(None)
        self.mmodel = FakeMicroscopeGUI()
        self.view = self.mmodel.focussedView.value
        self.canvas = DblMicroscopeCanvas(self.frame)
        self.canvas.backgroundBrush = wx.SOLID # no special background
        self.canvas.setView(self.view, self.mmodel)

        self.frame.SetSize((124, 124))
        test.gui_loop()
        self.frame.Show(True)
        test.gui_loop()

    def tearDown(self):
        self.frame.Destroy()
        self.app.MainLoop()

    def test_CrosHair(self):
        # crosshair
        show_crosshair = self.view.show_crosshair
        show_crosshair.value = True
        self.assertGreaterEqual(len(self.canvas.ViewOverlays), 1)
        lvo = len(self.canvas.ViewOverlays)
        show_crosshair.value = True
        self.assertEqual(len(self.canvas.ViewOverlays), lvo)
        show_crosshair.value = False
        self.assertEqual(len(self.canvas.ViewOverlays), lvo - 1)

    def test_BasicDisplay(self):
        """
        Draws a view with two streams, one with a red pixel with a low density
         and one with a blue pixel at a high density.
        """
        mpp = 0.0001
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)

        # add images
        im1 = wx.EmptyImage(11, 11, clear=True)
        px1_cent = (5, 5)
        # Red pixel at center, (5,5)
        im1.SetRGB(px1_cent[0], px1_cent[1], 255, 0, 0)
        im2 = wx.EmptyImage(201, 201, clear=True)
        px2_cent = ((im2.Width - 1) // 2 , (im2.Height - 1) // 2)
        # Blue pixel at center (100,100)
        im2.SetRGB(px2_cent[0], px2_cent[1], 0, 0, 255)
        stream1 = StaticStream("s1", InstrumentalImage(im1, mpp * 10, (0, 0)))
        # 200, 200 => outside of the im1
        # +(0.5, 0.5) to make it really in the center of the pixel
        stream2 = StaticStream("s2", InstrumentalImage(
                                        im2,
                                        mpp,
                                        (200.5 * mpp, 200.5 * mpp)))
        self.view.addStream(stream1)
        self.view.addStream(stream2)

        # reset the mpp of the view, as it's automatically set to the first
        # image
        self.view.mpp.value = mpp

        shift = (63, 63)
        self.canvas.ShiftView(shift)

        # merge the images
        ratio = 0.5
        self.view.merge_ratio.value = ratio
        self.assertEqual(ratio, self.view.merge_ratio.value)

        test.gui_loop()
        # it's supposed to update in less than 0.5s
        wx.MilliSleep(500)
        test.gui_loop()

        # copy the buffer into a nice image here
        resultIm = GetImageFromBuffer(self.canvas)
#        for i in range(resultIm.GetWidth()):
#            for j in range(resultIm.GetHeight()):
#                px = GetRGB(resultIm, i, j)
#                if px != (0, 0, 0):
#                    print px, i, j

        px1 = GetRGB(resultIm, resultIm.Width / 2 + shift[0], resultIm.Height / 2 + shift[1])
        self.assertEqual(px1, (127, 0, 0))
        px2 = GetRGB(resultIm, resultIm.Width / 2 + 200 + shift[0],
                               resultIm.Height / 2 + 200 + shift[1])
        self.assertEqual(px2, (0, 0, 255))

        # remove first picture
        self.view.removeStream(stream1)
        test.gui_loop()
        wx.MilliSleep(500)
        test.gui_loop()

        resultIm = GetImageFromBuffer(self.canvas)
        px2 = GetRGB(resultIm, resultIm.Width / 2 + 200 + shift[0], resultIm.Height / 2 + 200 + shift[1])
        self.assertEqual(px2, (0, 0, 255))

#    @unittest.skip("simple")
    def test_BasicMove(self):
        mpp = 0.0001
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)

        # add images
        im1 = wx.EmptyImage(11, 11, clear=True)
        px1_cent = (5, 5)
        im1.SetRGB(px1_cent[0], px1_cent[1], 255, 0, 0) # Red pixel at center, (5,5)
        im2 = wx.EmptyImage(201, 201, clear=True)
        px2_cent = (100, 100)
        im2.SetRGB(px2_cent[0], px2_cent[1], 0, 0, 255) # Blue pixel at center (100,100)
        stream1 = StaticStream("s1", InstrumentalImage(im1, mpp * 10, (0, 0)))
        stream2 = StaticStream("s2", InstrumentalImage(im2, mpp, (200.5 * mpp, 200.5 * mpp)))
        self.view.addStream(stream1)
        self.view.addStream(stream2)
        # view might set its mpp to the mpp of first image => reset it
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)

        shift = (100, 100)
        self.canvas.ShiftView(shift)

        # merge the images
        ratio = 0.5
        self.view.merge_ratio.value = ratio
        self.assertEqual(ratio, self.view.merge_ratio.value)

        test.gui_loop()
        # it's supposed to update in less than 1s
        wx.MilliSleep(500)
        test.gui_loop()

        # copy the buffer into a nice image here
        resultIm = GetImageFromBuffer(self.canvas)

        px1 = GetRGB(resultIm, resultIm.Width / 2 + shift[0], resultIm.Height / 2 + shift[1])
        self.assertEqual(px1, (127, 0, 0))
        px2 = GetRGB(resultIm, resultIm.Width / 2 + 200 + shift[0], resultIm.Height / 2 + 200 + shift[1])
        self.assertEqual(px2, (0, 0, 255))

#    @unittest.skip("simple")
    def test_ZoomMove(self):
        mpp = 0.0001
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)

        # add images
        im1 = wx.EmptyImage(11, 11, clear=True)
        px1_cent = (5, 5)
        im1.SetRGB(px1_cent[0], px1_cent[1], 255, 0, 0) # Red pixel at center, (5,5)
        stream1 = StaticStream("s1", InstrumentalImage(im1, mpp * 10, (0, 0)))
        self.view.addStream(stream1)
        # view might set its mpp to the mpp of first image => reset it
        self.view.mpp.value = mpp

        shift = (10, 10)
        self.canvas.ShiftView(shift)

        test.gui_loop()
        wx.MilliSleep(500)
        test.gui_loop()
        resultIm = GetImageFromBuffer(self.canvas)

        px1 = GetRGB(resultIm,
                     self.canvas._bmp_buffer_size[0] / 2 + 10,
                     self.canvas._bmp_buffer_size[1] / 2 + 10)
        self.assertEqual(px1, (255, 0, 0))

        # zoom in
        self.canvas.Zoom(2)
        self.assertEqual(mpp / (2 ** 2), self.view.mpp.value)
        test.gui_loop()
        wx.MilliSleep(500)
        test.gui_loop()
        resultIm = GetImageFromBuffer(self.canvas)

        px1 = GetRGB(resultIm,
             self.canvas._bmp_buffer_size[0] / 2 + 40,
             self.canvas._bmp_buffer_size[1] / 2 + 40)
        self.assertEqual(px1, (255, 0, 0))


if __name__ == "__main__":
    unittest.main()


def GetRGB(im, x, y):
    # TODO use DC.GetPixel()
    return (im.GetRed(x, y), im.GetGreen(x, y), im.GetBlue(x, y))

def GetImageFromBuffer(canvas):
    """
    Copy the current buffer into a wx.Image
    """
    resultBmp = wx.EmptyBitmap(*canvas._bmp_buffer_size)
    resultDC = wx.MemoryDC()
    resultDC.SelectObject(resultBmp)
    resultDC.BlitPointSize((0, 0), canvas._bmp_buffer_size, canvas._dc_buffer, (0, 0))
    resultDC.SelectObject(wx.NullBitmap)
    return wx.ImageFromBitmap(resultBmp)

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
