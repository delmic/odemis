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
    
    def testExportOnePage(self):
        # create a simple greyscale image
        shape = (512, 512)
        dtype = numpy.uint16
        data = model.DataArray(numpy.zeros(shape, dtype))

        # export
        tiff.export(FILENAME, data)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        self.assertEqual(im.size, shape)
        
        os.remove(FILENAME)

#    @skip("Doesn't work")
    def testExportMultiPage(self):
        # create a simple greyscale image
        shape = (512, 512)
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            ldata.append(model.DataArray(numpy.zeros(shape, dtype)))

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
            self.assertEqual(im.size, shape)
            
        os.remove(FILENAME)

    def testExportThumbnail(self):
        # create a simple greyscale image
        shape = (512, 512)
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            ldata.append(model.DataArray(numpy.zeros(shape, dtype)))

        # thumbnail : small RGB completly red
        tshape = (shape[0]/8, shape[1]/8, 3)
        tdtype = numpy.uint8
        thumbnail = numpy.zeros(tshape, tdtype)
        thumbnail[:, :, 0] += 255 # red
        
        # export
        tiff.export(FILENAME, ldata, thumbnail)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        
        # first page should be thumbnail
        im.seek(0)
        self.assertEqual(im.size, tshape[0:2])
        
        # check the number of pages
        for i in range(num):
            im.seek(i+1)
            self.assertEqual(im.size, shape)
            
        os.remove(FILENAME)
        
if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()