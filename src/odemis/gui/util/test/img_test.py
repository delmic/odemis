'''
Created on 19 Sep 2012

@author: piel
'''
from odemis.gui.util.img import DataArray2wxImage, wxImage2NDImage
import numpy
import unittest
import wx

def GetRGB(im, x, y):
    """
    return the r,g,b tuple corresponding to a pixel
    """
    r = im.GetRed(x, y)
    g = im.GetGreen(x, y)
    b = im.GetBlue(x, y)
    
    return (r, g, b)
    

class TestDataArray2wxImage(unittest.TestCase):
    def test_simple(self):
        # test with everything auto
        size = (1024, 512)
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint16") + 1500 
        
        # one colour
        out = DataArray2wxImage(grey_img)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 1)
        
        # add black
        grey_img[0, 0] = 0
        out = DataArray2wxImage(grey_img)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 2)
        
        # add white
        grey_img[0, 1] = 4095
        out = DataArray2wxImage(grey_img)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel0 = GetRGB(out, 0, 0)
        pixel1 = GetRGB(out, 1, 0)
        pixelg = GetRGB(out, 2, 0)
        self.assertGreater(pixel1, pixel0)
        self.assertGreater(pixelg, pixel0)
        self.assertGreater(pixel1, pixelg)
        
    
    def test_bc_0(self):
        """test with fixed brightness and contrast to 0"""
        # first 8 bit => no change
        size = (1024, 1024)
        depth = 256
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint8") + depth/2
        grey_img[0, 0] = 10
        grey_img[0, 1] = depth - 10
        
        # should keep the grey 
        out = DataArray2wxImage(grey_img, depth, 0, 0)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel = GetRGB(out, 2, 2)
        self.assertTrue(pixel == (127, 127, 127) or pixel == (128, 128, 128))
        
        # 16 bits
        depth = 4096
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint16") + depth/2 
        grey_img[0, 0] = 100
        grey_img[0, 1] = depth - 100
        
        # should keep the grey
        out = DataArray2wxImage(grey_img, depth, 0, 0)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel = GetRGB(out, 2, 2)
        self.assertTrue(pixel == (127, 127, 127) or pixel == (128, 128, 128)) 
        
    def test_bc_forced(self):
        """test with brightness and contrast to specific corner values"""
        size = (1024, 1024)
        depth = 4096
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint16") + depth/2 
        grey_img[0, 0] = 100
        grey_img[0, 1] = depth - 100
        
        # little change in brightness and contrast => still 3 colours
        out = DataArray2wxImage(grey_img, depth, brightness=0.1, contrast=0.1)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel0 = GetRGB(out, 0, 0)
        pixel1 = GetRGB(out, 1, 0)
        pixelg = GetRGB(out, 2, 0)
        self.assertGreater(pixel1, pixel0)
        self.assertGreater(pixelg, pixel0)
        self.assertGreater(pixel1, pixelg)
                
        # brightness == 1 => all white
        out = DataArray2wxImage(grey_img, depth, brightness=1, contrast=0)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 1)
        pixel = GetRGB(out, 2, 2)
        self.assertTrue(pixel == (255, 255, 255)) 
        
        # brightness == -1 => all black
        out = DataArray2wxImage(grey_img, depth, brightness=-1, contrast=0)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 1)
        pixel = GetRGB(out, 2, 2)
        self.assertTrue(pixel == (0, 0, 0))

        # contrast == -1 => all grey
        out = DataArray2wxImage(grey_img, depth, brightness=0, contrast=-1)
        self.assertEqual(out.GetSize(), size)
        # can be 2 colours : 127 and 128, depending on rounding 
        hist = wx.ImageHistogram()
        numcol = out.ComputeHistogram(hist)
        self.assertLessEqual(numcol, 2)
        if numcol == 1:
            self.assertTrue(hist.GetCountRGB(127, 127, 127) > 0 or
                            hist.GetCountRGB(128, 128, 128) > 0)
        elif numcol == 2:
            self.assertTrue(hist.GetCountRGB(127, 127, 127) > 0 and
                            hist.GetCountRGB(128, 128, 128) > 0)
        # contrast == 1 => black/white/grey (max)
        out = DataArray2wxImage(grey_img, depth, brightness=0, contrast=1)
        self.assertEqual(out.GetSize(), size)
        self.assertLessEqual(out.CountColours(), 3)


    def test_tint(self):
        """test with tint"""
        size = (1024, 1024)
        depth = 4096
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint16") + depth/2 
        grey_img[0, 0] = 0
        grey_img[0, 1] = depth - 1
        
        # white should become same as the tint
        tint = (0, 127, 255)
        out = DataArray2wxImage(grey_img, depth, brightness=0, contrast=0, tint=tint)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel0 = GetRGB(out, 0, 0)
        pixel1 = GetRGB(out, 1, 0)
        pixelg = GetRGB(out, 2, 0)
        self.assertEqual(pixel1, tint)
        self.assertGreater(pixel1, pixel0)
        self.assertGreater(pixelg, pixel0)
        self.assertGreater(pixel1, pixelg)
                
class TestWxImage2NDImage(unittest.TestCase):
    
    def test_simple(self):
        size = (32, 64)
        wximage = wx.EmptyImage(*size) # black RGB
        ndimage = wxImage2NDImage(wximage)
        self.assertEqual(ndimage.shape[0:2], size[-1:-3:-1])
        self.assertEqual(ndimage.shape[2], 3) # RGB
        self.assertTrue((ndimage[0,0] == [0, 0, 0]).all())
    
    # TODO alpha channel

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.test']
    unittest.main()