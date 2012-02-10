#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 10 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from dblmscopecanvas import DblMicroscopeCanvas
from draggablecanvas import WorldToBufferPoint
import time
import unittest
import wx

def loop():
    app = wx.GetApp()
    if app is None:
        return
    
    while True:
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()
        if not app.Pending():
            break

class TestDblMicroscopeCanvas(unittest.TestCase):

    def setUp(self):
        self.app = wx.PySimpleApp()
        self.frame = wx.Frame(None)
        self.canvas = DblMicroscopeCanvas(self.frame)
        self.frame.SetSize((124, 124))
        loop()
        self.frame.Show(True)
        loop()
        
    def tearDown(self):
        self.frame.Destroy()
        self.app.MainLoop()

    def test_CrosHair(self):
        # crosshair
        self.canvas.SetCrossHair(True)
        self.assertTrue(self.canvas.HasCrossHair())
        self.canvas.SetCrossHair(False)
        self.assertFalse(self.canvas.HasCrossHair())
        
    def test_BasicDisplay(self):
        ppm = 0.0001
        self.canvas.SetMPP(ppm)
        self.assertEqual(ppm, self.canvas.GetMPP())
        
        # add images
        im1 = wx.EmptyImage(11, 11, clear=True)
        im1.SetRGB(5, 5, 255, 0, 0) # Red pixel at center, (5,5)
        im2 = wx.EmptyImage(201, 201, clear=True)
        im2.SetRGB(100, 100, 0, 0, 255) # Blue pixel at center (100,100)
        self.canvas.SetImage(0, im1, (0,0), ppm * 10)
        self.canvas.SetImage(1, im2, (200,200), ppm)
        for i in range(im2.GetWidth()):
            print i
            for j in range(im2.GetHeight()):
                px = GetRGB(im2, i, j)
                if px != (0,0,0):
                    print px, i, j
                    
        # merge the images
        ratio = 0.5
        self.canvas.SetMergeRatio(ratio)
        self.assertEqual(ratio, self.canvas.GetMergeRatio())
        
        loop()
        # it's supposed to update in less than 1s
        time.sleep(1)
        loop()

        # copy the buffer into a nice image here
        resultBmp = wx.EmptyBitmap(*self.canvas.buffer_size)
        resultDC = wx.MemoryDC()
        resultDC.SelectObject(resultBmp)
        resultDC.BlitPointSize((0,0), self.canvas.buffer_size, self.canvas._dcBuffer, (0,0))
        resultDC.SelectObject(wx.NullBitmap)
        resultIm = wx.ImageFromBitmap(resultBmp)

#        for i in range(resultIm.GetWidth()):
#            print i
#            for j in range(resultIm.GetHeight()):
#                px = GetRGB(resultIm, i, j)
#                if px != (0,0,0):
#                    print px, i, j
#        
        px1 = GetRGB(resultIm, self.canvas.buffer_size[0]/2, self.canvas.buffer_size[1]/2)
        self.assertEqual(px1, (127, 0, 0))
        px2 = GetRGB(resultIm, self.canvas.buffer_size[0]/2 + 200, self.canvas.buffer_size[1]/2 + 200)
        self.assertEqual(px2, (0, 0, 255))

        # remove first picture
        self.canvas.SetImage(0, None)
        loop()

if __name__ == "__main__":
    unittest.main()
    
def GetRGB(im, x, y):
    return (im.GetRed(x,y), im.GetGreen(x,y), im.GetBlue(x,y))

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
