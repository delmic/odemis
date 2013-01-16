# -*- coding: utf-8 -*-
'''
Created on 14 Jan 2013

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS F

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from __future__ import division
from odemis import model
from odemis.dataio import hdf5
from unittest.case import skip
import h5py
import numpy
import os
import time
import unittest

FILENAME = "test" + hdf5.EXTENSIONS[0] 
class TestHDF5IO(unittest.TestCase):
    
    # Be careful: numpy's notation means that the pixel coordinates are Y,X,C
    def testExportOnePage(self):
        # create a simple greyscale image
        size = (256, 512) # (width, height)
        dtype = numpy.uint16
        data = model.DataArray(numpy.zeros(size[-1:-3:-1], dtype))
        white = (12, 52) # non symmetric position
        # less that 2**15 so that we don't have problem with PIL.getpixel() always returning an signed int
        data[white[-1:-3:-1]] = 124
        
        # export
        hdf5.export(FILENAME, data)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        
        f = h5py.File(FILENAME, "r")
        # need to transform to a full numpy.array just to remove the dimensions
        im = numpy.array(f["Acquisition0/ImageData/Image"])
        im.shape = im.shape[3:5]
        self.assertEqual(im.shape, data.shape)
        self.assertEqual(im[white[-1:-3:-1]], data[white[-1:-3:-1]])
        
        os.remove(FILENAME)

#    @skip("Doesn't work")
    def testExportMultiPage(self):
        # create a simple greyscale image
        size = (512, 256)
        white = (12, 52) # non symmetric position
        dtype = numpy.uint8
        ldata = []
        num = 2
        for i in range(num):
            a = model.DataArray(numpy.zeros(size[-1:-3:-1], dtype))
            a[white[-1:-3:-1]] = 124
            ldata.append(a)

        # export
        hdf5.export(FILENAME, ldata)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        f = h5py.File(FILENAME, "r")
        
        # check the number of pages
        for i in range(num):
            im = numpy.array(f["Acquisition%d/ImageData/Image" % i])
            im.shape = im.shape[3:5]
            self.assertEqual(im.shape, size[-1:-3:-1])
            self.assertEqual(im[white[-1:-3:-1]], 124)
            
        os.remove(FILENAME)
        
#    @skip("Doesn't work")
    def testExportThumbnail(self):
        # create a simple greyscale image
        size = (512, 256)
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            ldata.append(model.DataArray(numpy.zeros(size[-1:-3:-1], dtype)))

        # thumbnail : small RGB completely red
        tshape = (size[1]//8, size[0]//8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 0] += 255 # red
        blue = (12, 22) # non symmetric position
        thumbnail[blue[-1:-3:-1]] = [0,0,255]
        
        # export
        hdf5.export(FILENAME, ldata, thumbnail)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        f = h5py.File(FILENAME, "r")
        
        # look for the thumbnail
        im = f["Preview/Image"]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0,0].tolist(), [255,0,0])
        self.assertEqual(im[blue[-1:-3:-1]].tolist(), [0,0,255])
        
        # FIXME: color dimension should be C, and in order: Y, X, C
        
        # check the number of pages
        for i in range(num):
            im = numpy.array(f["Acquisition%d/ImageData/Image" % i])
            im.shape = im.shape[3:5]
            self.assertEqual(im.shape, size[-1:-3:-1])
            
        os.remove(FILENAME)
        
    def testMetadata(self):
        """
        checks that the metadata is saved with every picture
        """
        size = (512, 256, 1)
        dtype = numpy.dtype("uint16")
        metadata = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "fake hw",
                    model.MD_DESCRIPTION: "test",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    model.MD_EXP_TIME: 1.2, #s
                    model.MD_IN_WL: (500e-9, 520e-9), #m
                    }
        
        data = model.DataArray(numpy.zeros((size[1], size[0]), dtype), metadata=metadata)     
        
        # export
        hdf5.export(FILENAME, data)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        f = h5py.File(FILENAME, "r")

        # check format
        im = f["Acquisition0/ImageData/Image"]
        self.assertEqual(im.attrs["IMAGE_SUBCLASS"], "IMAGE_GRAYSCALE")
        
        # check basic metadata
        self.assertEqual(im.dims[4].label, "X")
        yres = im.dims[3][0][()] # second last dimension (Y), first scale, first and only value
        self.assertAlmostEqual(metadata[model.MD_PIXEL_SIZE][1], yres)
        ypos = f["Acquisition0/ImageData/YOffset"][()]
        self.assertAlmostEqual(metadata[model.MD_POS][1], ypos)
        
        # Check physical metadata
        desc = f["Acquisition0/PhysicalData/Title"][()]
        self.assertAlmostEqual(metadata[model.MD_DESCRIPTION], desc)
        
        
        iwl = f["Acquisition0/PhysicalData/ExcitationWavelength"][()] # m
        self.assertTrue((metadata[model.MD_IN_WL][0] <= iwl and 
                         iwl <= metadata[model.MD_IN_WL][1]))
        
        os.remove(FILENAME)
        
if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()