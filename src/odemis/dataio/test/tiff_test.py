#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 14 Sep 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis import model
from odemis.dataio import tiff
from unittest.case import skip
import Image
import numpy
import os
import unittest

FILENAME = "test" + tiff.EXTENSIONS[0] 
class TestTiffIO(unittest.TestCase):
    
    # Be careful: numpy's notation means that the pixel coordinates are Y,X,C
    def testExportOnePage(self):
        # create a simple greyscale image
        size = (256, 512)
        dtype = numpy.uint16
        data = model.DataArray(numpy.zeros(size[-1:-3:-1], dtype))
        white = (12, 52) # non symmetric position
        # less that 2**15 so that we don't have problem with PIL.getpixel() always returning an signed int
        data[white[-1:-3:-1]] = 124
        
        # export
        tiff.export(FILENAME, data)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        self.assertEqual(im.size, size)
        self.assertEqual(im.getpixel(white), 124)
        
        os.remove(FILENAME)

#    @skip("Doesn't work")
    def testExportMultiPage(self):
        # create a simple greyscale image
        size = (512, 256)
        white = (12, 52) # non symmetric position
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            a = model.DataArray(numpy.zeros(size[-1:-3:-1], dtype))
            a[white[-1:-3:-1]] = 124
            ldata.append(a)

        # export
        tiff.export(FILENAME, ldata)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        
        # check the number of pages
        for i in range(num):
            im.seek(i)
            self.assertEqual(im.size, size)
            self.assertEqual(im.getpixel(white), 124)
            
        os.remove(FILENAME)

    def testExportThumbnail(self):
        # create a simple greyscale image
        size = (512, 256)
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            ldata.append(model.DataArray(numpy.zeros(size[-1:-3:-1], dtype)))

        # thumbnail : small RGB completely red
        tshape = (size[1]/8, size[0]/8, 3)
        tdtype = numpy.uint8
        thumbnail = numpy.zeros(tshape, tdtype)
        thumbnail[:, :, 0] += 255 # red
        blue = (12, 22) # non symmetric position
        thumbnail[blue[-1:-3:-1]] = [0,0,255]
        
        # export
        tiff.export(FILENAME, ldata, thumbnail)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        
        # first page should be thumbnail
        im.seek(0)
        self.assertEqual(im.size, (tshape[1], tshape[0]))
        self.assertEqual(im.getpixel((0,0)), (255,0,0))
        self.assertEqual(im.getpixel(blue), (0,0,255))
        
        # check the number of pages
        for i in range(num):
            im.seek(i+1)
            self.assertEqual(im.size, size)
            
        os.remove(FILENAME)
        
if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()