#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 14 Sep 2012

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
'''
from __future__ import division

import Image
import libtiff
import logging
import numpy
from numpy.polynomial import polynomial
from odemis import model
import odemis
from odemis.dataio import tiff
from odemis.util import img
import os
import re
import time
import unittest
from unittest.case import skip

import libtiff.libtiff_ctypes as T # for the constant names
import xml.etree.ElementTree as ET


logging.getLogger().setLevel(logging.DEBUG)

FILENAME = u"test" + tiff.EXTENSIONS[0]
class TestTiffIO(unittest.TestCase):

#     def tearDown(self):
#         # clean up
#         try:
#             os.remove(FILENAME)
#         except Exception:
#             pass

    # Be careful: numpy's notation means that the pixel coordinates are Y,X,C
#    @skip("simple")
    def testExportOnePage(self):
        # create a simple greyscale image
        size = (256, 512)
        dtype = numpy.uint16
        data = model.DataArray(numpy.zeros(size[::-1], dtype))
        white = (12, 52) # non symmetric position
        # less that 2**15 so that we don't have problem with PIL.getpixel() always returning an signed int
        data[white[::-1]] = 124
        
        # export
        tiff.export(FILENAME, data)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        self.assertEqual(im.size, size)
        self.assertEqual(im.getpixel(white), 124)


    def testUnicodeName(self):
        """Try filename not fitting in ascii"""
        # create a simple greyscale image
        size = (256, 512)
        dtype = numpy.uint16
        data = model.DataArray(numpy.zeros(size[::-1], dtype))
        white = (12, 52) # non symmetric position
        # less that 2**15 so that we don't have problem with PIL.getpixel() always returning an signed int
        data[white[::-1]] = 124

        fn = u"𝔸𝔹ℂ" + FILENAME
        # export
        tiff.export(fn, data)

        # check it's here
        st = os.stat(fn) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(fn)
        self.assertEqual(im.format, "TIFF")
        self.assertEqual(im.size, size)
        self.assertEqual(im.getpixel(white), 124)

        os.remove(fn)
        
#    @skip("simple")
    def testExportMultiPage(self):
        # create a simple greyscale image
        size = (512, 256)
        white = (12, 52) # non symmetric position
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            a = model.DataArray(numpy.zeros(size[::-1], dtype))
            a[white[::-1]] = 124
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

#    @skip("simple")
    def testExportThumbnail(self):
        # create a simple greyscale image
        size = (512, 256)
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            ldata.append(model.DataArray(numpy.zeros(size[::-1], dtype)))

        # thumbnail : small RGB completely red
        tshape = (size[1]//8, size[0]//8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 0] += 255 # red
        blue = (12, 22) # non symmetric position
        thumbnail[blue[::-1]] = [0, 0, 255]
        
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

        # check OME-TIFF metadata
        imo = libtiff.tiff.TIFFfile(FILENAME)
        omemd = imo.IFD[0].get_value("ImageDescription")
        self.assertTrue(omemd.startswith('<?xml') or omemd[:4].lower() == '<ome')

        # remove "xmlns" which is the default namespace and is appended everywhere
        omemd = re.sub('xmlns="http://www.openmicroscopy.org/Schemas/OME/....-.."',
                       "", omemd, count=1)
        root = ET.fromstring(omemd)

        # check the IFD of each TIFFData is different
        ifds = set()
        for tdt in root.findall("Image/Pixels/TiffData"):
            ifd = int(tdt.get("IFD", "0"))
            self.assertNotIn(ifd, ifds, "Multiple times the same IFD %d" % ifd)
            self.assertTrue(imo.IFD[ifd], "IFD %d doesn't exists" % ifd)


    def testExportCube(self):
        """
        Check it's possible to export a 3D data (typically: 2D area with full
         spectrum for each point)
        """
        dtype = numpy.dtype("uint16")
        size3d = (512, 256, 220) # X, Y, C
        size = (512, 256)
        metadata3d = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "fake spec",
                    model.MD_HW_VERSION: "1.23",
                    model.MD_SW_VERSION: "aa 4.56",
                    model.MD_DESCRIPTION: "test3d",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 1), # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_WL_POLYNOMIAL: [500e-9, 1e-9], # m, m/px: wl polynomial
                    model.MD_POS: (1e-3, -30e-3), # m
                    model.MD_EXP_TIME: 1.2, #s
                    model.MD_IN_WL: (500e-9, 520e-9), #m
                    }
        metadata = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: u"", # check empty unicode strings
                    model.MD_DESCRIPTION: u"tÉst", # tiff doesn't support É (but XML does)
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 2), # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    model.MD_EXP_TIME: 1.2, #s
                    model.MD_IN_WL: (500e-9, 520e-9), #m
                    }
        ldata = []
        # 3D data generation (+ metadata): gradient along the wavelength
        data3d = numpy.empty(size3d[-1::-1], dtype=dtype)
        end = 2**metadata3d[model.MD_BPP]
        step = end // size3d[2]
        lin = numpy.arange(0, end, step, dtype=dtype)[:size3d[2]]
        lin.shape = (size3d[2], 1, 1) # to be able to copy it on the first dim
        data3d[:] = lin
        # introduce Time and Z dimension to state the 3rd dim is channel
        data3d = data3d[:, numpy.newaxis, numpy.newaxis,:,:] 
        ldata.append(model.DataArray(data3d, metadata3d))
        
        # an additional 2D data, for the sake of it
        ldata.append(model.DataArray(numpy.zeros(size[-1::-1], dtype), metadata))

        # export
        tiff.export(FILENAME, ldata)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        
        # check the 3D data (one image per channel)
        for i in range(size3d[2]):
            im.seek(i)
            self.assertEqual(im.size, size3d[0:2])
            self.assertEqual(im.getpixel((1,1)), i * step)
            
        # check the 2D data
        im.seek(i + 1)
        self.assertEqual(im.size, size)
        self.assertEqual(im.getpixel((1,1)), 0)


    def testExportNoWL(self):
        """
        Check it's possible to export/import a spectrum with missing wavelength
        info
        """
        dtype = numpy.dtype("uint16")
        size3d = (512, 256, 220) # X, Y, C
        size = (512, 256)
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "bad spec",
                    model.MD_DESCRIPTION: "test3d",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 1), # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_WL_POLYNOMIAL: [0], # m, m/px: missing polynomial
                    model.MD_POS: (1e-3, -30e-3), # m
                    model.MD_EXP_TIME: 1.2, #s
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: u"", # check empty unicode strings
                    model.MD_DESCRIPTION: u"tÉst", # tiff doesn't support É (but XML does)
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 2), # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    model.MD_EXP_TIME: 1.2, #s
                    model.MD_IN_WL: (500e-9, 520e-9), #m
                    }]
        ldata = []
        # 3D data generation (+ metadata): gradient along the wavelength
        data3d = numpy.empty(size3d[::-1], dtype=dtype)
        end = 2 ** metadata[0][model.MD_BPP]
        step = end // size3d[2]
        lin = numpy.arange(0, end, step, dtype=dtype)[:size3d[2]]
        lin.shape = (size3d[2], 1, 1) # to be able to copy it on the first dim
        data3d[:] = lin
        # introduce Time and Z dimension to state the 3rd dim is channel
        data3d = data3d[:, numpy.newaxis, numpy.newaxis, :, :]
        ldata.append(model.DataArray(data3d, metadata[0]))

        # an additional 2D data, for the sake of it
        ldata.append(model.DataArray(numpy.zeros(size[::-1], dtype), metadata[1]))

        # export
        tiff.export(FILENAME, ldata)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            md = metadata[i]
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            numpy.testing.assert_allclose(im.metadata[model.MD_POS], md[model.MD_POS], rtol=1e-4)
            numpy.testing.assert_allclose(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertAlmostEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE], delta=1)
            self.assertEqual(im.metadata[model.MD_BPP], md[model.MD_BPP])
            self.assertEqual(im.metadata[model.MD_BINNING], md[model.MD_BINNING])

            if model.MD_WL_POLYNOMIAL in md:
                pn = md[model.MD_WL_POLYNOMIAL]
                # either identical, or nothing at all
                if model.MD_WL_POLYNOMIAL in im.metadata:
                    numpy.testing.assert_allclose(im.metadata[model.MD_WL_POLYNOMIAL], pn)
                else:
                    self.assertNotIn(model.MD_WL_LIST, im.metadata)


#    @skip("simple")
    def testMetadata(self):
        """
        checks that the metadata is saved with every picture
        """
        size = (512, 256, 1)
        dtype = numpy.dtype("uint64")
        metadata = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "fake hw",
                    model.MD_HW_VERSION: "2.54",
                    model.MD_DESCRIPTION: "test",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 2), # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    model.MD_EXP_TIME: 1.2, #s
                    model.MD_IN_WL: (500e-9, 520e-9), #m
                    }
        
        data = model.DataArray(numpy.zeros((size[1], size[0]), dtype), metadata=metadata)     
        
        # export
        tiff.export(FILENAME, data)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        imo = libtiff.tiff.TIFFfile(FILENAME)
        self.assertEqual(len(imo.IFD), 1, "Tiff file doesn't contain just one image")

        ifd = imo.IFD[0]
        # check format        
        self.assertEqual(size[2], ifd.get_value("SamplesPerPixel"))
        # BitsPerSample is the actual format, not model.MD_BPP
        self.assertEqual(dtype.itemsize * 8, ifd.get_value("BitsPerSample")[0])
        self.assertEqual(T.SAMPLEFORMAT_UINT, ifd.get_value("SampleFormat")[0])
        
        # check metadata
        self.assertEqual("Odemis " + odemis.__version__, ifd.get_value("Software"))
        self.assertEqual(metadata[model.MD_HW_NAME], ifd.get_value("Make"))
        self.assertEqual(metadata[model.MD_HW_VERSION] + " (driver %s)" % metadata[model.MD_SW_VERSION],
                         ifd.get_value("Model"))
        self.assertEqual(metadata[model.MD_DESCRIPTION], ifd.get_value("PageName"))
        yres = rational2float(ifd.get_value("YResolution"))
        self.assertAlmostEqual(1 / metadata[model.MD_PIXEL_SIZE][1], yres * 100)
        ypos = rational2float(ifd.get_value("YPosition"))
        self.assertAlmostEqual(metadata[model.MD_POS][1], (ypos / 100) - 1)
        
        # check OME-TIFF metadata
        omemd = imo.IFD[0].get_value("ImageDescription")
        self.assertTrue(omemd.startswith('<?xml') or omemd[:4].lower()=='<ome')
        
        # remove "xmlns" which is the default namespace and is appended everywhere
        omemd = re.sub('xmlns="http://www.openmicroscopy.org/Schemas/OME/....-.."',
                       "", omemd, count=1)
        root = ET.fromstring(omemd)
#        ns = {"ome": root.tag.rsplit("}")[0][1:]} # read the default namespace
        roottag = root.tag.split("}")[-1]
        self.assertEqual(roottag.lower(), "ome")
        
        detect_name = root.find("Instrument/Detector").get("Model")
        self.assertEqual(metadata[model.MD_HW_NAME], detect_name)
        
        self.assertEqual(len(root.findall("Image")), 1)
        ime = root.find("Image")
        ifdn = int(ime.find("Pixels/TiffData").get("IFD", "0"))
        self.assertEqual(ifdn, 0)
        sx = int(ime.find("Pixels").get("SizeX")) # px
        self.assertEqual(size[0], sx)
        psx = float(ime.find("Pixels").get("PhysicalSizeX")) # um
        self.assertAlmostEqual(metadata[model.MD_PIXEL_SIZE][0], psx * 1e-6)
        exp = float(ime.find("Pixels/Plane").get("ExposureTime")) # s
        self.assertAlmostEqual(metadata[model.MD_EXP_TIME], exp)
        
        iwl = float(ime.find("Pixels/Channel").get("ExcitationWavelength")) # nm
        iwl *= 1e-9
        self.assertTrue((metadata[model.MD_IN_WL][0] <= iwl and 
                         iwl <= metadata[model.MD_IN_WL][1]))
        
        bin_str = ime.find("Pixels/Channel/DetectorSettings").get("Binning")
        exp_bin = "%dx%d" % metadata[model.MD_BINNING]
        self.assertEqual(bin_str, exp_bin)

#    @skip("simple")
    def testExportRead(self):
        """
        Checks that we can read back an image and a thumbnail
        """
        # create 2 simple greyscale images
        sizes = [(512, 256), (500, 400)] # different sizes to ensure different acquisitions
        dtype = numpy.dtype("uint16")
        white = (12, 52) # non symmetric position
        ldata = []
        num = 2
        # TODO: check support for combining channels when same data shape
        for i in range(num):
            a = model.DataArray(numpy.zeros(sizes[i][-1:-3:-1], dtype))
            a[white[-1:-3:-1]] = 1027
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 0] += 255 # red
        blue = (12, 22) # non symmetric position
        thumbnail[blue[-1:-3:-1]] = [0, 0, 255]

        # export
        tiff.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), num)

        for i, im in enumerate(rdata):
            if len(im.shape) > 2:
                subim = im[0, 0, 0] # remove C,T,Z dimensions
            else:
                subim = im      # TODO: should it always be 5 dim?
            self.assertEqual(subim.shape, sizes[i][-1::-1])
            self.assertEqual(subim[white[-1:-3:-1]], ldata[i][white[-1:-3:-1]])

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [255, 0, 0])
        self.assertEqual(im[blue[-1:-3:-1]].tolist(), [0, 0, 255])

#    @skip("simple")
    def testReadMDSpec(self):
        """
        Checks that we can read back the metadata of a spectrum image
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "test",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2), # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                     model.MD_POS: (13.7e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1.2, # s
                     model.MD_IN_WL: (500e-9, 520e-9), # m
                     model.MD_OUT_WL: (650e-9, 660e-9, 675e-9, 678e-9, 680e-9), # m
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake spec",
                     model.MD_DESCRIPTION: "test3d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1), # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                     model.MD_WL_POLYNOMIAL: [500e-9, 1e-9], # m, m/px: wl polynomial
                     model.MD_POS: (13.7e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1.2, # s
                    },
                    ]
        # create 2 simple greyscale images
        sizes = [(512, 256), (500, 400, 1, 1, 220)] # different sizes to ensure different acquisitions
        dtype = numpy.dtype("uint8")
        ldata = []
        for i, s in enumerate(sizes):
            a = model.DataArray(numpy.zeros(s[::-1], dtype), metadata[i])
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255 # green

        # export
        tiff.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            md = metadata[i]
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            numpy.testing.assert_allclose(im.metadata[model.MD_POS], md[model.MD_POS], rtol=1e-4)
            numpy.testing.assert_allclose(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertAlmostEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE], delta=1)
            self.assertEqual(im.metadata[model.MD_BPP], md[model.MD_BPP])
            self.assertEqual(im.metadata[model.MD_BINNING], md[model.MD_BINNING])

            if model.MD_WL_POLYNOMIAL in md:
                pn = md[model.MD_WL_POLYNOMIAL]
                # 2 formats possible
                if model.MD_WL_LIST in im.metadata:
                    l = ldata[i].shape[0]
                    npn = polynomial.Polynomial(pn,
                                    domain=[0, l - 1],
                                    window=[0, l - 1])
                    wl = npn.linspace(l)[1]
                    numpy.testing.assert_allclose(im.metadata[model.MD_WL_LIST], wl)
                else:
                    numpy.testing.assert_allclose(im.metadata[model.MD_WL_POLYNOMIAL], pn)

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    def testReadMDAR(self):
        """
        Checks that we can read back the metadata of an Angular Resolved image
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "sem survey",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2), # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                     model.MD_POS: (1e-3, -30e-3), # m
                     model.MD_DWELL_TIME: 1e-6,  # s
                     model.MD_LENS_MAG: 1200, # ratio
                     model.MD_EBEAM_VOLTAGE: 10000,  # V
                     model.MD_EBEAM_CURRENT: 2.6,  # A
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake ccd",
                     model.MD_DESCRIPTION: "AR",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1), # px, px
                     model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6), # m/px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                     model.MD_POS: (1.2e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1.2, # s
                     model.MD_AR_POLE: (253.1, 65.1), # px
                     model.MD_AR_XMAX: 12e-3,
                     model.MD_AR_HOLE_DIAMETER: 0.6e-3,
                     model.MD_AR_FOCUS_DISTANCE: 0.5e-3,
                     model.MD_AR_PARABOLA_F: 2e-3,
                     model.MD_LENS_MAG: 60, # ratio
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake ccd",
                     model.MD_DESCRIPTION: "AR",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1), # px, px
                     model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6), # m/px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                     model.MD_POS: (1e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1.2, # s
                     model.MD_AR_POLE: (253.1, 65.1), # px
                     model.MD_AR_XMAX: 12e-3,
                     model.MD_AR_HOLE_DIAMETER: 0.6e-3,
                     model.MD_AR_FOCUS_DISTANCE: 0.5e-3,
                     model.MD_AR_PARABOLA_F: 2e-3,
                     model.MD_LENS_MAG: 60, # ratio
                    },
                    ]
        # create 2 simple greyscale images
        sizes = [(512, 256), (500, 400), (500, 400)] # different sizes to ensure different acquisitions
        dtype = numpy.dtype("uint16")
        ldata = []
        for s, md in zip(sizes, metadata):
            a = model.DataArray(numpy.zeros(s[::-1], dtype), md)
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255 # green

        # export
        tiff.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for im, md in zip(rdata, metadata):
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            numpy.testing.assert_allclose(im.metadata[model.MD_POS], md[model.MD_POS], rtol=1e-4)
            numpy.testing.assert_allclose(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertAlmostEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE], delta=1)
            if model.MD_AR_POLE in md:
                numpy.testing.assert_allclose(im.metadata[model.MD_AR_POLE], md[model.MD_AR_POLE])
            if model.MD_AR_XMAX in md:
                self.assertAlmostEqual(im.metadata[model.MD_AR_XMAX], md[model.MD_AR_XMAX])
            if model.MD_AR_HOLE_DIAMETER in md:
                self.assertAlmostEqual(im.metadata[model.MD_AR_HOLE_DIAMETER], md[model.MD_AR_HOLE_DIAMETER])
            if model.MD_AR_FOCUS_DISTANCE in md:
                self.assertAlmostEqual(im.metadata[model.MD_AR_FOCUS_DISTANCE], md[model.MD_AR_FOCUS_DISTANCE])
            if model.MD_AR_PARABOLA_F in md:
                self.assertAlmostEqual(im.metadata[model.MD_AR_PARABOLA_F], md[model.MD_AR_PARABOLA_F])
            if model.MD_LENS_MAG in md:
                self.assertAlmostEqual(im.metadata[model.MD_LENS_MAG], md[model.MD_LENS_MAG])
            # if model.MD_EBEAM_CURRENT in md:
            #    self.assertEqual(im.metadata[model.MD_EBEAM_CURRENT], md[model.MD_EBEAM_CURRENT])
            if model.MD_EBEAM_VOLTAGE in md:
                self.assertEqual(im.metadata[model.MD_EBEAM_VOLTAGE], md[model.MD_EBEAM_VOLTAGE])

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

#    @skip("simple")
    def testReadMDFluo(self):
        """
        Checks that we can read back the metadata of a fluoresence image
        The OME-TIFF file will contain just one big array, but three arrays 
        should be read back with the right data.
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "brightfield",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1), # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6), # m/px
                     model.MD_POS: (13.7e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1.2, # s
                     model.MD_IN_WL: (400e-9, 630e-9), # m
                     model.MD_OUT_WL: (400e-9, 630e-9), # m
                     # correction metadata
                     model.MD_POS_COR: (-1e-6, 3e-6), # m
                     model.MD_PIXEL_SIZE_COR: (1.2, 1.2),
                     model.MD_ROTATION_COR: 6.27,  # rad
                     model.MD_SHEAR_COR: 0.005,
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "blue dye",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1), # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6), # m/px
                     model.MD_POS: (13.7e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1.2, # s
                     model.MD_IN_WL: (500e-9, 522e-9),  # m
                     model.MD_OUT_WL: (650e-9, 660e-9, 675e-9, 678e-9, 680e-9), # m
                     model.MD_USER_TINT: (255, 0, 65), # purple
                     model.MD_LIGHT_POWER: 100e-3 # W
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "green dye",
                     model.MD_ACQ_DATE: time.time() + 2,
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1), # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6), # m/px
                     model.MD_POS: (13.7e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1, # s
                     model.MD_IN_WL: (590e-9, 620e-9),  # m
                     model.MD_OUT_WL: (620e-9, 650e-9), # m
                     model.MD_ROTATION: 0.1,  # rad
                     model.MD_SHEAR: 0,
                     model.MD_BASELINE: 400.0
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "green dye",
                     model.MD_ACQ_DATE: time.time() + 2,
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1,  # s
                     model.MD_IN_WL: (600e-9, 630e-9),  # m
                     model.MD_OUT_WL: (620e-9, 650e-9),  # m
                     # In order to test shear is applied even without rotation
                     # provided. And also check that *_COR is merged into its
                     # normal metadata brother.
                     # model.MD_SHEAR: 0.03,
                     model.MD_SHEAR_COR: 0.003,
                    },
                    ]
        # create 3 greyscale images of same size
        size = (512, 256)
        dtype = numpy.dtype("uint16")
        ldata = []
        for i, md in enumerate(metadata):
            a = model.DataArray(numpy.zeros(size[::-1], dtype), md.copy())
            a[i, i] = i # "watermark" it
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (size[1] // 8, size[0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail.metadata[model.MD_DIMS] = "YXC"
        thumbnail.metadata[model.MD_POS] = (13.7e-3, -30e-3)
        thumbnail[:, :, 1] += 255 # green

        # export
        tiff.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        # TODO: rdata and ldata don't have to be in the same order
        for i, im in enumerate(rdata):
            md = metadata[i].copy()
            img.mergeMetadata(md)
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            numpy.testing.assert_allclose(im.metadata[model.MD_POS], md[model.MD_POS], rtol=1e-4)
            numpy.testing.assert_allclose(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertAlmostEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE], delta=1)
            self.assertEqual(im.metadata[model.MD_BPP], md[model.MD_BPP])
            self.assertEqual(im.metadata[model.MD_BINNING], md[model.MD_BINNING])
            if model.MD_USER_TINT in md:
                self.assertEqual(im.metadata[model.MD_USER_TINT], md[model.MD_USER_TINT])

            iwl = im.metadata[model.MD_IN_WL] # nm
            self.assertTrue((md[model.MD_IN_WL][0] <= iwl[0] and
                             iwl[1] <= md[model.MD_IN_WL][-1]),
                            "%s not in %s" % (iwl, md[model.MD_IN_WL]))

            owl = im.metadata[model.MD_OUT_WL] # nm
            self.assertTrue((md[model.MD_OUT_WL][0] <= owl[0] and
                             owl[1] <= md[model.MD_OUT_WL][-1]))
            if model.MD_LIGHT_POWER in md:
                self.assertEqual(im.metadata[model.MD_LIGHT_POWER], md[model.MD_LIGHT_POWER])

            self.assertAlmostEqual(im.metadata.get(model.MD_ROTATION, 0), md.get(model.MD_ROTATION, 0))
            self.assertAlmostEqual(im.metadata.get(model.MD_BASELINE, 0), md.get(model.MD_BASELINE, 0))
            self.assertAlmostEqual(im.metadata.get(model.MD_SHEAR, 0), md.get(model.MD_SHEAR, 0))

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

#    @skip("simple")
    def testReadMDOutWlBands(self):
        """
        Checks that we hand MD_OUT_WL if it contains multiple bands.
        OME supports only one value, so it's ok to discard some info.
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "blue dye",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                     model.MD_OUT_WL: ((650e-9, 660e-9), (675e-9, 680e-9)),  # m
                     model.MD_USER_TINT: (255, 0, 65),  # purple
                     model.MD_LIGHT_POWER: 100e-3  # W
                    },
                    ]
        size = (512, 256)
        dtype = numpy.dtype("uint16")
        ldata = []
        for i, md in enumerate(metadata):
            a = model.DataArray(numpy.zeros(size[::-1], dtype), md.copy())
            a[i, i] = i  # "watermark" it
            a[i + 1, i + 5] = i + 1  # "watermark" it
            ldata.append(a)

        # export
        tiff.export(FILENAME, ldata)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            self.assertEqual(im[i + 1, i + 5], i + 1)

        im = rdata[0]

        emd = metadata[0].copy()
        rmd = im.metadata
        img.mergeMetadata(emd)
        img.mergeMetadata(rmd)
        self.assertEqual(rmd[model.MD_DESCRIPTION], emd[model.MD_DESCRIPTION])
        iwl = rmd[model.MD_IN_WL]  # nm
        self.assertTrue((emd[model.MD_IN_WL][0] <= iwl[0] and
                         iwl[1] <= emd[model.MD_IN_WL][-1]))

        # It should be within at least one of the bands
        owl = rmd[model.MD_OUT_WL]  # nm
        for eowl in emd[model.MD_OUT_WL]:
            if (eowl[0] <= owl[0] and owl[1] <= eowl[-1]):
                break
        else:
            self.fail("Out wl %s is not within original metadata" % (owl,))

    def testReadMDMnchr(self):
        """
        Checks that we can read back the metadata of a monochromator image.
        It's 32 bits, and the same shape as the ETD
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake monochromator",
                     model.MD_SAMPLES_PER_PIXEL: 1,
                     model.MD_DESCRIPTION: "test",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_HW_VERSION: "Unknown",
                     model.MD_DWELL_TIME: 0.001,  # s
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (1.2e-3, -30e-3),  # m
                     model.MD_LENS_MAG: 100,  # ratio
                     model.MD_OUT_WL: (2.8e-07, 3.1e-07)
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_VERSION: "Unknown",
                     model.MD_SAMPLES_PER_PIXEL: 1,
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "etd",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_LENS_MAG: 100,  # ratio
                     model.MD_DWELL_TIME: 1e-06,  # s
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_VERSION: "Unknown",
                     model.MD_SAMPLES_PER_PIXEL: 1,
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "Anchor region",
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (10e-3, 30e-3),  # m
                     model.MD_LENS_MAG: 100,  # ratio
                     model.MD_AD_LIST: (1437117571.733935, 1437117571.905051),
                     model.MD_DWELL_TIME: 1e-06,  # s
                    },
                    ]
        # create 3 greyscale images
        ldata = []
        mnchr_size = (6, 5)
        sem_size = (128, 128)
        # Monochromator
        mnchr_dtype = numpy.dtype("uint32")
        a = model.DataArray(numpy.zeros(mnchr_size[::-1], mnchr_dtype), metadata[0])
        ldata.append(a)
        # Normal SEM
        sem_dtype = numpy.dtype("uint16")
        b = model.DataArray(numpy.zeros(mnchr_size[::-1], sem_dtype), metadata[1])
        ldata.append(b)
        # Anchor data
        c = model.DataArray(numpy.zeros(sem_size[::-1], sem_dtype), metadata[2])
        ldata.append(c)

        # export
        tiff.export(FILENAME, ldata)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            md = metadata[i].copy()
            img.mergeMetadata(md)
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertAlmostEqual(im.metadata[model.MD_POS][0], md[model.MD_POS][0])
            self.assertAlmostEqual(im.metadata[model.MD_POS][1], md[model.MD_POS][1])
            self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE][0], md[model.MD_PIXEL_SIZE][0])
            self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE][1], md[model.MD_PIXEL_SIZE][1])

        # Check that output wavelength range was correctly read back
        owl = rdata[0].metadata[model.MD_OUT_WL]  # nm
        md = metadata[0]
        self.assertTrue((md[model.MD_OUT_WL][0] <= owl[0] and
                         owl[1] <= md[model.MD_OUT_WL][-1]))

    def testRGB(self):
        """
        Checks that can both write and read back an RGB image
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "my exported image",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_DIMS: "YXC",
                    },
                    ]
        # TODO: test without alpha channel and with different DIMS order
        shape = (5120, 2560, 4)
        dtype = numpy.dtype("uint8")
        ldata = []
        for i, md in enumerate(metadata):
            a = model.DataArray(numpy.zeros(shape, dtype), md.copy())
            a[:, :, 3] = 255  # no transparency
            a[i, i] = i  # "watermark" it
            a[i + 1, i + 5] = i + 1  # "watermark" it
            ldata.append(a)

        # export
        tiff.export(FILENAME, ldata)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            for j in range(shape[-1]):
                self.assertEqual(im[i + 1, i + 5, j], i + 1)

            self.assertEqual(im.shape, shape)
            emd = metadata[i].copy()
            rmd = im.metadata
            img.mergeMetadata(emd)
            img.mergeMetadata(rmd)
            self.assertEqual(rmd[model.MD_DESCRIPTION], emd[model.MD_DESCRIPTION])
            self.assertEqual(rmd[model.MD_DIMS], emd[model.MD_DIMS])
            self.assertAlmostEqual(rmd[model.MD_POS][0], emd[model.MD_POS][0])
            self.assertAlmostEqual(rmd[model.MD_POS][1], emd[model.MD_POS][1])
            self.assertAlmostEqual(rmd[model.MD_PIXEL_SIZE][0], emd[model.MD_PIXEL_SIZE][0])
            self.assertAlmostEqual(rmd[model.MD_PIXEL_SIZE][1], emd[model.MD_PIXEL_SIZE][1])

    def testReadMDTime(self):
        """
        Checks that we can read back the metadata of an acquisition with time correlation
        """
        shapes = [(512, 256), (1, 5220, 1, 50, 40), (1, 512, 1, 1, 1)]
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "test",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake time correlator",
                     model.MD_DESCRIPTION: "test3d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 16,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-6),  # m/px
                     model.MD_PIXEL_DUR: 1e-9,  # s
                     model.MD_TIME_OFFSET:-20e-9,  # s, of the first time value
                     model.MD_OUT_WL: "pass-through",
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake time correlator",
                     model.MD_DESCRIPTION: "test1d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 16,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_PIXEL_DUR: 10e-9,  # s
                     model.MD_TIME_OFFSET:-500e-9,  # s, of the first time value
                     model.MD_OUT_WL: (500e-9, 600e-9),
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                    },
                    ]
        # create 1 simple greyscale image
        ldata = []
        a = model.DataArray(numpy.zeros(shapes[0], numpy.uint16), metadata[0])
        ldata.append(a)
        # Create 2D time correlated image
        a = model.DataArray(numpy.zeros(shapes[1], numpy.uint32), metadata[1])
        a[:, :, :, 1, 5] = 1
        a[0, 10, 0, 1, 0] = 10000
        ldata.append(a)
        # Create time correlated spot acquisition
        a = model.DataArray(numpy.zeros(shapes[2], numpy.uint32), metadata[2])
        a[0, 10, 0, 0, 0] = 20000
        ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (400, 300, 3)
        thumbnail = model.DataArray(numpy.zeros(tshape, numpy.uint8))
        thumbnail[:, :, 1] += 255  # green

        # export
        tiff.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            md = metadata[i]
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertAlmostEqual(im.metadata[model.MD_POS][0], md[model.MD_POS][0])
            self.assertAlmostEqual(im.metadata[model.MD_POS][1], md[model.MD_POS][1])
            self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE][0], md[model.MD_PIXEL_SIZE][0])
            self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE][1], md[model.MD_PIXEL_SIZE][1])
            self.assertAlmostEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE], delta=1)
            if model.MD_LENS_MAG in md:
                self.assertEqual(im.metadata[model.MD_LENS_MAG], md[model.MD_LENS_MAG])

            # None of the images are using light => no MD_IN_WL
            self.assertFalse(model.MD_IN_WL in im.metadata,
                             "Reporting excitation wavelength while there is none")

            if model.MD_PIXEL_DUR in md:
                pxd = md[model.MD_PIXEL_DUR]
                self.assertAlmostEqual(im.metadata[model.MD_PIXEL_DUR], pxd)
            if model.MD_TIME_OFFSET in md:
                tof = md[model.MD_TIME_OFFSET]
                self.assertAlmostEqual(im.metadata[model.MD_TIME_OFFSET], tof)

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])


def rational2float(rational):
    """
    Converts a rational number (from libtiff) to a float
    rational (numpy array of shape 1 with numer and denom fields): num,denom
    """
    return rational["numer"][0] / rational["denom"][0]

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
