#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 10 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import time
import unittest

import wx

from odemis import model
from odemis.gui import instrmodel
from odemis.gui.dblmscopecanvas import DblMicroscopeCanvas
from odemis.gui.model.img import InstrumentalImage
from odemis.gui.model.stream import StaticStream


def loop():
    app = wx.GetApp()
    if app is None:
        return

    while True:
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()
        if not app.Pending():
            break

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
        self.canvas.setView(self.view, self.mmodel)

        self.frame.SetSize((124, 124))
        loop()
        self.frame.Show(True)
        loop()

    def tearDown(self):
        self.frame.Destroy()
        self.app.MainLoop()

    def test_CrosHair(self):
        # crosshair
        show_crosshair = self.view.show_crosshair
        show_crosshair.value = True
        self.assertTrue(len(self.canvas.ViewOverlays) == 1)
        show_crosshair.value = True
        self.assertTrue(len(self.canvas.ViewOverlays) == 1)
        show_crosshair.value = False
        self.assertTrue(len(self.canvas.ViewOverlays) == 0)

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
        px2_cent = (100, 100)
        # Blue pixel at center (100,100)
        im2.SetRGB(px2_cent[0], px2_cent[1], 0, 0, 255)
        stream1 = StaticStream("s1", InstrumentalImage(im1, mpp * 10, (0, 0)))
        # 200, 200 => outside of the im1
        stream2 = StaticStream("s2", InstrumentalImage(
                                        im2,
                                        mpp,
                                        (200 * mpp, 200 * mpp)))
        self.view.addStream(stream1)
        self.view.addStream(stream2)

        # reset the mpp of the view, as it's automatically set to the first
        # image
        self.view.mpp.value = mpp

        # for now it fails: depending on shift (sometimes everything is shifted
        # by -1,-1)
        shift = (0, 0) # 63,63 ; 100, 100 work
        # self.canvas.ShiftView(shift)

        # merge the images
        ratio = 0.5
        self.view.merge_ratio.value = ratio
        self.assertEqual(ratio, self.view.merge_ratio.value)

        loop()
        # it's supposed to update in less than 1s
        time.sleep(1)
        loop()

        # copy the buffer into a nice image here
        resultIm = GetImageFromBuffer(self.canvas)
        # for i in range(resultIm.GetWidth()):
        #     for j in range(resultIm.GetHeight()):
        #         px = GetRGB(resultIm, i, j)
        #         if px != (0,0,0):
        #             print px, i, j

        px1 = GetRGB(resultIm, resultIm.Width/2 + shift[0], resultIm.Height/2 + shift[1])
        self.assertEqual(px1, (127, 0, 0))
        px2 = GetRGB(resultIm, resultIm.Width/2 + 200 + shift[0], resultIm.Height/2 + 200 + shift[1])
        self.assertEqual(px2, (0, 0, 255))

        # remove first picture
        self.view.removeStream(stream1)
        loop()
        time.sleep(1)
        loop()

        resultIm = GetImageFromBuffer(self.canvas)
        px2 = GetRGB(resultIm, resultIm.Width/2 + 200 + shift[0], resultIm.Height/2 + 200 + shift[1])
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
        stream2 = StaticStream("s2", InstrumentalImage(im2, mpp, (200 * mpp, 200 * mpp)))
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

        loop()
        # it's supposed to update in less than 1s
        time.sleep(1)
        loop()

        # copy the buffer into a nice image here
        resultIm = GetImageFromBuffer(self.canvas)

        px1 = GetRGB(resultIm, resultIm.Width/2 + shift[0], resultIm.Height/2 + shift[1])
        self.assertEqual(px1, (127, 0, 0))
        px2 = GetRGB(resultIm, resultIm.Width/2 + 200 + shift[0], resultIm.Height/2 + 200 + shift[1])
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

        loop()
        time.sleep(1)
        loop()
        resultIm = GetImageFromBuffer(self.canvas)

        px1 = GetRGB(resultIm,
                     self.canvas.buffer_size[0]/2 + 10,
                     self.canvas.buffer_size[1]/2 + 10)
        self.assertEqual(px1, (255, 0, 0))

        # zoom in
        self.canvas.Zoom(2)
        self.assertEqual(mpp / (2 ** 2), self.view.mpp.value)
        loop()
        time.sleep(1)
        loop()
        resultIm = GetImageFromBuffer(self.canvas)

        px1 = GetRGB(resultIm,
             self.canvas.buffer_size[0]/2 + 40,
             self.canvas.buffer_size[1]/2 + 40)
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
    resultBmp = wx.EmptyBitmap(*canvas.buffer_size)
    resultDC = wx.MemoryDC()
    resultDC.SelectObject(resultBmp)
    resultDC.BlitPointSize((0, 0), canvas.buffer_size, canvas._dcBuffer, (0, 0))
    resultDC.SelectObject(wx.NullBitmap)
    return wx.ImageFromBitmap(resultBmp)

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
