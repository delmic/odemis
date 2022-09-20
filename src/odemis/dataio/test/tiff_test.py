#!/usr/bin/env python3
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
# Don't import unicode_literals to avoid issues with external functions. Code works on python2 and python3.
from PIL import Image
import libtiff
import logging
import numpy
from odemis import model
import odemis
from odemis.acq.stream import POL_POSITIONS, POL_POSITIONS_RESULTS
from odemis.dataio import tiff
from odemis.util import img
import os
import re
import time
import unittest
from unittest.case import skip
import json

import libtiff.libtiff_ctypes as T # for the constant names
import xml.etree.ElementTree as ET

logging.getLogger().setLevel(logging.DEBUG)

FILENAME = u"test" + tiff.EXTENSIONS[0]
class TestTiffIO(unittest.TestCase):

    def tearDown(self):
        # clean up
        try:
            os.remove(FILENAME)
        except Exception:
            pass

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

        del im
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
            im.seek(i + 1)
            self.assertEqual(im.size, size)
        del im

        # check OME-TIFF metadata
        imo = libtiff.TIFF.open(FILENAME)
        omemd = imo.GetField("ImageDescription")
        self.assertTrue(omemd.startswith(b'<?xml') or omemd[:4].lower() == b'<ome')

        # remove "xmlns" which is the default namespace and is appended everywhere
        omemd = re.sub(b'xmlns="http://www.openmicroscopy.org/Schemas/OME/....-.."',
                       b"", omemd, count=1)
        root = ET.fromstring(omemd)

        # check the IFD of each TIFFData is different
        ifds = set()
        for tdt in root.findall("Image/Pixels/TiffData"):
            ifd = int(tdt.get("IFD", "0"))
            self.assertNotIn(ifd, ifds, "Multiple times the same IFD %d" % ifd)
            self.assertEqual(imo.SetDirectory(ifd), 1, "IFD %d doesn't exists" % ifd)

        imo.close()

    def testExportCube(self):
        """
        Check it's possible to export a 3D data (typically: 2D area with full
         spectrum for each point)
        """
        dtype = numpy.dtype("uint16")
        size3d = (512, 256, 220) # X, Y, C
        size = (512, 256)
        sizes = [(512, 256), (100, 110, 1, 10, 20)]
        metadata3d = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "fake spec",
                    model.MD_HW_VERSION: "1.23",
                    model.MD_DESCRIPTION: "test3d",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 1), # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(sizes[1][-1])],
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
            self.assertEqual(im.getpixel((1, 1)), i * step)

        # check the 2D data
        im.seek(i + 1)
        self.assertEqual(im.size, size)
        self.assertEqual(im.getpixel((1, 1)), 0)

    def testExportSpatialCube(self):
        """
        Check it's possible to export a 3D data (typically: 2D area with full
         spectrum for each point)
        """
        dtype = numpy.dtype("uint16")
        size3d = (512, 256, 220)  # X, Y, Z
        size = (512, 256)
        sizes = [(512, 256), (100, 110, 1, 10, 20)]
        metadata3d = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "fake spec",
                    model.MD_DIMS: "ZYX",
                    model.MD_HW_VERSION: "1.23",
                    model.MD_DESCRIPTION: "test3dspatial",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 1),  # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5, 3e-4),  # m/px
                    model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(sizes[1][-1])],
                    model.MD_POS: (1e-3, -30e-3, -5e-3),  # m
                    model.MD_EXP_TIME: 1.2,  # s
                    model.MD_IN_WL: (500e-9, 520e-9),  # m
                    }
        metadata = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "",  # check empty unicode strings
                    model.MD_DIMS: "ZYX",
                    model.MD_DESCRIPTION: "test",  # tiff doesn't support É (but XML does)
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 2),  # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                    model.MD_POS: (1e-3, -30e-3),  # m
                    model.MD_EXP_TIME: 1.2,  # s
                    model.MD_IN_WL: (500e-9, 520e-9),  # m
                    }
        ldata = []
        # 3D data generation (+ metadata): gradient along the wavelength
        data3d = numpy.empty(size3d[-1::-1], dtype=dtype)
        end = 2 ** metadata3d[model.MD_BPP]
        step = end // size3d[2]
        lin = numpy.arange(0, end, step, dtype=dtype)[:size3d[2]]
        lin.shape = (size3d[2], 1, 1)  # to be able to copy it on the first dim
        data3d[:] = lin
        # introduce Time and Z dimension to state the 3rd dim is channel
        data3d = data3d[:, numpy.newaxis, numpy.newaxis, :, :]
        ldata.append(model.DataArray(data3d, metadata3d))

        # an additional 2D data, for the sake of it
        ldata.append(model.DataArray(numpy.zeros(size[-1::-1], dtype), metadata))

        # export
        tiff.export(FILENAME, ldata)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")

        # check the 3D data (one image per channel)
        for i in range(size3d[2]):
            im.seek(i)
            self.assertEqual(im.size, size3d[0:2])
            self.assertEqual(im.getpixel((1, 1)), i * step)

        # check the 2D data
        im.seek(i + 1)
        self.assertEqual(im.size, size)
        self.assertEqual(im.getpixel((1, 1)), 0)

        # Check the metadata
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        im = rdata[0]
        self.assertEqual(im.metadata[model.MD_DESCRIPTION], metadata3d[model.MD_DESCRIPTION])
        numpy.testing.assert_allclose(im.metadata[model.MD_POS], metadata3d[model.MD_POS], rtol=1e-4)
        numpy.testing.assert_allclose(im.metadata[model.MD_PIXEL_SIZE], metadata3d[model.MD_PIXEL_SIZE])
        self.assertAlmostEqual(im.metadata[model.MD_ACQ_DATE], metadata3d[model.MD_ACQ_DATE], delta=1)

        im = rdata[1]
        self.assertEqual(im.metadata[model.MD_DESCRIPTION], metadata[model.MD_DESCRIPTION])
        numpy.testing.assert_allclose(im.metadata[model.MD_POS], metadata[model.MD_POS], rtol=1e-4)
        numpy.testing.assert_allclose(im.metadata[model.MD_PIXEL_SIZE], metadata[model.MD_PIXEL_SIZE])
        self.assertAlmostEqual(im.metadata[model.MD_ACQ_DATE], metadata[model.MD_ACQ_DATE], delta=1)

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
                    model.MD_WL_LIST: [],
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

#    @skip("simple")
    def testMetadata(self):
        """
        checks that the metadata is saved with every picture
        """
        size = (512, 256, 1)
        dtype = numpy.dtype("uint64")
        # use list instead of tuple for binning because json converts tuples to lists anyway
        extra_md = {"Camera" : {'binning' : (0, 0)}, u"¤³ß": {'</Image>': '</Image>'},
                    "Fake component": ("parameter", None)}
        exp_extra_md = json.loads(json.dumps(extra_md))  # slightly different for MD_EXTRA_SETTINGS (tuples are converted to lists)

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
                    model.MD_EXTRA_SETTINGS : extra_md,
                    }

        data = model.DataArray(numpy.zeros((size[1], size[0]), dtype), metadata=metadata)

        # export
        tiff.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        imo = libtiff.TIFF.open(FILENAME)
        self.assertEqual(imo.SetDirectory(1), 0, "Tiff file doesn't contain just one image")

        # check format
        # self.assertEqual(size[2], imo.GetField("SamplesPerPixel"))
        # BitsPerSample is the actual format, not model.MD_BPP
        self.assertEqual(dtype.itemsize * 8, imo.GetField("BitsPerSample"))
        self.assertEqual(T.SAMPLEFORMAT_UINT, imo.GetField("SampleFormat"))

        # check metadata
        self.assertEqual("Odemis " + odemis.__version__, imo.GetField("Software").decode('utf-8'))
        self.assertEqual(metadata[model.MD_HW_NAME], imo.GetField("Make").decode('utf-8'))
        self.assertEqual(metadata[model.MD_HW_VERSION] + " (driver %s)" % metadata[model.MD_SW_VERSION],
                         imo.GetField("Model").decode('utf-8'))
        self.assertEqual(metadata[model.MD_DESCRIPTION], imo.GetField("PageName").decode('utf-8'))
        yres = imo.GetField("YResolution")
        self.assertAlmostEqual(1 / metadata[model.MD_PIXEL_SIZE][1], yres * 100)
        ypos = imo.GetField("YPosition")
        self.assertAlmostEqual(metadata[model.MD_POS][1], (ypos / 100) - 1)

        # check OME-TIFF metadata
        omemd = imo.GetField("ImageDescription").decode('utf-8')
        self.assertTrue(omemd.startswith('<?xml') or omemd[:4].lower() == '<ome')

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
        self.assertTrue((metadata[model.MD_IN_WL][0] <= iwl <= metadata[model.MD_IN_WL][1]))

        bin_str = ime.find("Pixels/Channel/DetectorSettings").get("Binning")
        exp_bin = "%dx%d" % metadata[model.MD_BINNING]
        self.assertEqual(bin_str, exp_bin)

        self.assertEqual(json.loads(ime.find("ExtraSettings").text), exp_extra_md)
        imo.close()

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
    def testReadAndSaveMDSpec(self):
        """
        Checks that we can save and read back the metadata of a spectrum image.
        """
        # TODO may write a loop once testing for polynomial and once for WL_LIST?
        # create 2 simple greyscale images (sem overview, Spec): XY, XYZTC (XY ebeam pos scanned, C Spec info)
        sizes = [(512, 256), (100, 110, 1, 1, 200)]  # different sizes to ensure different acquisitions
        # Create fake current over time report
        cot = [(time.time(), 1e-12)]
        for i in range(1, 171):
            cot.append((cot[0][0] + i, i * 1e-12))

        # use list instead of tuple for binning because json converts tuples to lists anyway
        extra_md = {"Camera" : {'binning' : [0, 0]}, u"¤³ß": {'</Image>': '</Image>'},
                    "Fake component": ("parameter", None)}
        exp_extra_md = json.loads(json.dumps(extra_md))  # slightly different for MD_EXTRA_SETTINGS (tuples are converted to lists)

        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "test spectrum",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2, # s
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                     model.MD_OUT_WL: (650e-9, 660e-9, 675e-9, 678e-9, 680e-9), # m
                     model.MD_EBEAM_CURRENT: 20e-6,  # A
                     model.MD_EXTRA_SETTINGS: extra_md,
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake spec",
                     model.MD_DESCRIPTION: "test3d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_EBEAM_CURRENT_TIME: cot,
                     model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(sizes[1][-1])],
                     model.MD_EXTRA_SETTINGS: extra_md,
                    },
                    ]
        dtype = numpy.dtype("uint8")
        ldata = []
        for i, s in enumerate(sizes):
            a = model.DataArray(numpy.zeros(s[::-1], dtype), metadata[i])
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255  # green

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
            self.assertEqual(im.metadata[model.MD_EXTRA_SETTINGS], exp_extra_md)

            if model.MD_EBEAM_CURRENT_TIME in md:
                ocot = md[model.MD_EBEAM_CURRENT_TIME]
                rcot = im.metadata[model.MD_EBEAM_CURRENT_TIME]
                assert len(ocot) == len(rcot)
                for (od, oc), (rd, rc) in zip(ocot, rcot):
                    self.assertAlmostEqual(od, rd, places=6)
                    self.assertAlmostEqual(oc, rc)

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    #    @skip("simple")
    def testReadAndSaveMDTempSpec(self):
        """
        Checks that we can save and read back the metadata of a temporal spectrum image.
        """
        # create 2 simple greyscale images (sem overview, tempSpec): XY, XYZTC (XY ebeam pos scanned, TC tempSpec image)
        sizes = [(512, 256), (100, 110, 1, 10, 20)]  # different sizes to ensure different acquisitions
        # Create fake current over time report
        cot = [(time.time(), 1e-12)]
        for i in range(1, 171):
            cot.append((cot[0][0] + i, i * 1e-12))

        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "test temporal spectrum",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                     model.MD_OUT_WL: (650e-9, 660e-9, 675e-9, 678e-9, 680e-9),  # m
                     model.MD_EBEAM_CURRENT: 20e-6,  # A
                     },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake temp spec",
                     model.MD_DESCRIPTION: "test3d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_INTEGRATION_COUNT: 1,
                     model.MD_EBEAM_CURRENT_TIME: cot,
                     model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(sizes[1][-1])],
                     model.MD_TIME_LIST: [1e-6 * i for i in range(sizes[1][-2])],
                     model.MD_STREAK_MCPGAIN: 3,
                     model.MD_STREAK_MODE: True,
                     model.MD_STREAK_TIMERANGE: 0.000000001,  # sec
                     model.MD_TRIGGER_DELAY: 0.0000001,  # sec
                     model.MD_TRIGGER_RATE: 1000000,  # Hz
                     },
                    ]
        dtype = numpy.dtype("uint8")
        ldata = []
        for i, s in enumerate(sizes):
            a = model.DataArray(numpy.zeros(s[::-1], dtype), metadata[i])
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
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
            numpy.testing.assert_allclose(im.metadata[model.MD_POS], md[model.MD_POS], rtol=1e-4)
            numpy.testing.assert_allclose(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertAlmostEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE], delta=1)
            self.assertEqual(im.metadata[model.MD_BPP], md[model.MD_BPP])
            self.assertEqual(im.metadata[model.MD_BINNING], md[model.MD_BINNING])

            if model.MD_WL_LIST in md:
                wl = md[model.MD_WL_LIST]
                numpy.testing.assert_allclose(im.metadata[model.MD_WL_LIST], wl)
            if model.MD_TIME_LIST in md:
                tm = md[model.MD_TIME_LIST]
                numpy.testing.assert_allclose(im.metadata[model.MD_TIME_LIST], tm)
            if model.MD_STREAK_TIMERANGE in md:
                self.assertEqual(im.metadata[model.MD_STREAK_TIMERANGE], md[model.MD_STREAK_TIMERANGE])
            if model.MD_STREAK_MCPGAIN in md:
                self.assertEqual(im.metadata[model.MD_STREAK_MCPGAIN], md[model.MD_STREAK_MCPGAIN])
            if model.MD_STREAK_MODE in md:
                self.assertEqual(im.metadata[model.MD_STREAK_MODE], md[model.MD_STREAK_MODE])
            if model.MD_TRIGGER_DELAY in md:
                self.assertEqual(im.metadata[model.MD_TRIGGER_DELAY], md[model.MD_TRIGGER_DELAY])
            if model.MD_TRIGGER_RATE in md:
                self.assertEqual(im.metadata[model.MD_TRIGGER_RATE], md[model.MD_TRIGGER_RATE])
            if model.MD_INTEGRATION_COUNT in md:
                self.assertEqual(im.metadata[model.MD_INTEGRATION_COUNT], md[model.MD_INTEGRATION_COUNT])

            if model.MD_EBEAM_CURRENT_TIME in md:
                ocot = md[model.MD_EBEAM_CURRENT_TIME]
                rcot = im.metadata[model.MD_EBEAM_CURRENT_TIME]
                assert len(ocot) == len(rcot)
                for (od, oc), (rd, rc) in zip(ocot, rcot):
                    self.assertAlmostEqual(od, rd, places=6)
                    self.assertAlmostEqual(oc, rc)

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    #    @skip("simple")
    def testReadAndSaveMDSpecialDim(self):
        """
        Checks that we can save and read back the metadata of an image with dim CTZ=0.
        Some older images still have a dimension CTZ=1. For newer images saved with Odemis, dimension=1
        are initially already removed when saving.
        """
        # create 2 simple greyscale images (sem overview, tempSpec): XY, XYZTC (XY ebeam pos scanned, TCZ=0)
        # simulate an image, which has only 2 dim but MD TIME_LIST
        # (can happen for older images saved with Odemis, where CTZ=1)
        sizes = [(512, 256), (50, 60)]  # different sizes to ensure different acquisitions

        # Create fake current over time report
        cot = [(time.time(), 1e-12)]
        for i in range(1, 171):
            cot.append((cot[0][0] + i, i * 1e-12))

        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "test TCZ=0",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                     model.MD_OUT_WL: (650e-9, 660e-9, 675e-9, 678e-9, 680e-9),  # m
                     model.MD_EBEAM_CURRENT: 20e-6,  # A
                     },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake TCZ=0",
                     model.MD_DESCRIPTION: "test",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_EBEAM_CURRENT_TIME: cot,
                     model.MD_WL_LIST: [0.0],  # explicitly append a WL_LIST MD
                     model.MD_TIME_LIST: [0.0],  # explicitly append a TIME_LIST MD
                     },
                    ]
        dtype = numpy.dtype("uint8")
        ldata = []
        for i, s in enumerate(sizes):
            a = model.DataArray(numpy.zeros(s[::-1], dtype), metadata[i])
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255  # green

        # export image (MD_TIME_LIST should be not saved)
        tiff.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # read back data and check
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))
        # check time and wl list are not in MD anymore
        self.assertNotIn(model.MD_TIME_LIST, rdata[1].metadata)
        self.assertNotIn(model.MD_WL_LIST, rdata[1].metadata)

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    def testReadAndSaveMDAR(self):
        """
        Checks that we can read back the metadata of an Angular Resolved image
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "sem survey",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_DWELL_TIME: 1e-6,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                     model.MD_EBEAM_VOLTAGE: 10000,  # V
                     model.MD_EBEAM_CURRENT: 2.6,  # A
                     },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake ccd",
                     model.MD_DESCRIPTION: "AR",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6),  # m/px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (1.2e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_AR_POLE: (253.1, 65.1),  # px
                     model.MD_AR_XMAX: 12e-3,
                     model.MD_AR_HOLE_DIAMETER: 0.6e-3,
                     model.MD_AR_FOCUS_DISTANCE: 0.5e-3,
                     model.MD_AR_PARABOLA_F: 2e-3,
                     model.MD_LENS_MAG: 60,  # ratio
                     },
                    # same AR image MD but different beam pos (MD_POS)
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake ccd",
                     model.MD_DESCRIPTION: "AR",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6),  # m/px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_AR_POLE: (253.1, 65.1),  # px
                     model.MD_AR_MIRROR_TOP: (1253.1, 65.1),  # px, px/m
                     model.MD_AR_MIRROR_BOTTOM: (254, -451845.48),  # px, px/m
                     model.MD_AR_XMAX: 12e-3,
                     model.MD_AR_HOLE_DIAMETER: 0.6e-3,
                     model.MD_AR_FOCUS_DISTANCE: 0.5e-3,
                     model.MD_AR_PARABOLA_F: 2e-3,
                     model.MD_LENS_MAG: 60,  # ratio
                     },
                    ]
        # create 2 simple greyscale images
        sizes = [(512, 256), (500, 400), (500, 400)]  # different sizes to ensure different acquisitions
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
        st = os.stat(FILENAME)  # this test also that the file is created
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
            if model.MD_AR_MIRROR_TOP in md:
                numpy.testing.assert_allclose(im.metadata[model.MD_AR_MIRROR_TOP], md[model.MD_AR_MIRROR_TOP])
            if model.MD_AR_MIRROR_BOTTOM in md:
                numpy.testing.assert_allclose(im.metadata[model.MD_AR_MIRROR_BOTTOM], md[model.MD_AR_MIRROR_BOTTOM])
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
            if model.MD_EBEAM_CURRENT in md:
                self.assertEqual(im.metadata[model.MD_EBEAM_CURRENT], md[model.MD_EBEAM_CURRENT])
            if model.MD_EBEAM_VOLTAGE in md:
                self.assertEqual(im.metadata[model.MD_EBEAM_VOLTAGE], md[model.MD_EBEAM_VOLTAGE])

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    def testReadAndSaveMDARPolarization(self):
        """
        Checks that we can read back the metadata of an angular resolved image acquired with an polarization analyzer.
        """
        pol_pos = POL_POSITIONS
        qwp_pos = [0.0, 1.570796, 0.785398, 2.356194, 0.0, 0.0]
        linpol_pos = [0.0, 1.570796, 0.785398, 2.356194, 0.785398, 2.356194]
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "sem survey",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                     model.MD_EBEAM_VOLTAGE: 10000,  # V
                     model.MD_EBEAM_CURRENT: 2.6,  # A
                     }
                    ]

        for idx in range(len(pol_pos)):
            metadata.append({model.MD_SW_VERSION: "1.0-test",
                             model.MD_HW_NAME: "fake ccd",
                             model.MD_DESCRIPTION: "test ar polarization analyzer",
                             model.MD_ACQ_DATE: time.time(),
                             model.MD_BPP: 12,
                             model.MD_BINNING: (1, 1),  # px, px
                             model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6),  # m/px
                             model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                             model.MD_POS: (1.2e-3, -30e-3),  # m
                             model.MD_EXP_TIME: 1.2,  # s
                             model.MD_OUT_WL: (300e-9, 900e-9),  # m  (band pass filter)
                             model.MD_AR_POLE: (253.1, 65.1),
                             model.MD_AR_XMAX: 12e-3,
                             model.MD_AR_HOLE_DIAMETER: 0.6e-3,
                             model.MD_AR_FOCUS_DISTANCE: 0.5e-3,
                             model.MD_AR_PARABOLA_F: 2e-3,
                             model.MD_LENS_MAG: 60,  # ratio
                             model.MD_POL_MODE: pol_pos[idx],
                             model.MD_POL_POS_QWP: qwp_pos[idx],  # rad
                             model.MD_POL_POS_LINPOL: linpol_pos[idx],  # rad
                             })

        # create 2 simple greyscale images
        # different sizes to ensure different acquisitions
        sizes = [(512, 256), (500, 400), (500, 400), (500, 400), (500, 400), (500, 400), (500, 400)]  # different sizes to ensure different acquisitions
        dtype = numpy.dtype("uint16")
        ldata = []
        for s, md in zip(sizes, metadata):
            a = model.DataArray(numpy.zeros(s[::-1], dtype), md)
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255  # green

        # export
        tiff.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for im, md in zip(rdata, metadata):
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            numpy.testing.assert_allclose(im.metadata[model.MD_POS], md[model.MD_POS], rtol=1e-4)
            numpy.testing.assert_allclose(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            if model.MD_POL_MODE in md:
                self.assertEqual(im.metadata[model.MD_POL_MODE], md[model.MD_POL_MODE])
            if model.MD_POL_POS_QWP in md:
                self.assertEqual(im.metadata[model.MD_POL_POS_QWP], md[model.MD_POL_POS_QWP])
            if model.MD_POL_POS_LINPOL in md:
                self.assertEqual(im.metadata[model.MD_POL_POS_LINPOL], md[model.MD_POL_POS_LINPOL])

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    def testReadAndSaveMDARPolarimetry(self):
        """
        Checks that we can read back the metadata of an angular resolved image acquired with an polarization analyzer.
        """
        pol_pos = POL_POSITIONS_RESULTS
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "sem survey",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_DWELL_TIME: 1e-6,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                     model.MD_EBEAM_VOLTAGE: 10000,  # V
                     model.MD_EBEAM_CURRENT: 2.6,  # A
                     }
                    ]

        for idx in range(len(pol_pos)):
            metadata.append({model.MD_SW_VERSION: "1.0-test",
                             model.MD_HW_NAME: "fake ccd",
                             model.MD_DESCRIPTION: "test ar polarimetry",
                             model.MD_ACQ_DATE: time.time(),
                             model.MD_BPP: 12,
                             model.MD_BINNING: (1, 1),  # px, px
                             model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6),  # m/px
                             model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                             model.MD_POS: (1.2e-3, -30e-3),  # m
                             model.MD_EXP_TIME: 1.2,  # s
                             model.MD_OUT_WL: (300e-9, 900e-9),  # m  (band pass filter)
                             model.MD_AR_POLE: (253.1, 65.1),
                             model.MD_AR_XMAX: 12e-3,
                             model.MD_AR_HOLE_DIAMETER: 0.6e-3,
                             model.MD_AR_FOCUS_DISTANCE: 0.5e-3,
                             model.MD_AR_PARABOLA_F: 2e-3,
                             model.MD_LENS_MAG: 60,  # ratio
                             model.MD_POL_MODE: pol_pos[idx],
                             })

        # create 2 simple greyscale images
        # different sizes to ensure different acquisitions
        sizes = [(512, 256), (500, 400), (500, 400), (500, 400), (500, 400), (500, 400), (500, 400)]  # different sizes to ensure different acquisitions
        dtype = numpy.dtype("uint16")
        ldata = []
        for s, md in zip(sizes, metadata):
            a = model.DataArray(numpy.zeros(s[::-1], dtype), md)
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255  # green

        # export
        tiff.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for im, md in zip(rdata, metadata):
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            numpy.testing.assert_allclose(im.metadata[model.MD_POS], md[model.MD_POS], rtol=1e-4)
            numpy.testing.assert_allclose(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            if model.MD_POL_MODE in md:
                self.assertEqual(im.metadata[model.MD_POL_MODE], md[model.MD_POL_MODE])
            self.assertNotIn(model.MD_POL_POS_QWP, im.metadata)  # should be not in md
            self.assertNotIn(model.MD_POL_POS_QWP, im.metadata)  # should be not in md

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
                     model.MD_LIGHT_POWER: 100e-3  # W
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
            if model.MD_LIGHT_POWER in md and model.MD_LIGHT_POWER in im.metadata:
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

    def testReadMDHWNOTE(self):
        """
        Checks that we can read back the metadata of a Hardware Note (Microscope Setting Data)
        The OME-TIFF file will contain just one big array, but three arrays
        should be read back with the right data.
        """
        metadata = [
                    {
                    model.MD_HW_NOTE: ("Component 'EBeam Phenom':\n"
                                       "\trole: e-beam\n"
                                       "\taffects: 'BSED Phenom', 'Camera'\n"
                                       "\tshape (RO Attribute)	value: (1024, 1024)\n"
                                       "\tswVersion (RO Attribute)	value: 4.4.2\n"
                                       "\thwVersion (RO Attribute)	value: Phenom <G4>\n"
                                       "\tscale (Vigilant Attribute)	 value: (4, 4) (range: (0, 0) , (2048, 2048))\n"
                                       "\taccelVoltage (Vigilant Attribute)	 value: 5300 (unit: V) (range: 4797, 10000)\n"
                                       "\tspotSize (Vigilant Attribute)	 value: 2.7 (range: 2.1, 3.3)\n"),
                    model.MD_HW_NAME: "fake hw",
                    model.MD_DESCRIPTION: "blue dye",
                    model.MD_ACQ_DATE: time.time() + 1,
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 1),  # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                    model.MD_POS: (13.7e-3, -30e-3),  # m
                    model.MD_EXP_TIME: 1.2,  # s
                    model.MD_IN_WL: (500e-9, 522e-9),  # m
                    model.MD_OUT_WL: (650e-9, 660e-9, 675e-9, 678e-9, 680e-9),  # m
                    model.MD_USER_TINT: (255, 0, 65),  # purple
                    model.MD_LIGHT_POWER: 100e-3  # W
                    },
                    {
                    model.MD_HW_NOTE: ("Component 'EBeam Phenom':\n"
                                       "\trole: e-beam\n"
                                       "\taffects: 'BSED Phenom', 'Camera'\n"
                                       "\tshape (RO Attribute)	value: (2048, 2048)\n"
                                       "\tswVersion (RO Attribute)	value: 4.4.2\n"
                                       "\thwVersion (RO Attribute)	value: Phenom G4\n"
                                       "\tscale (Vigilant Attribute)	 value: (4, 4) (range: (0, 0) , (2048, 2048))\n"
                                       "\taccelVoltage (Vigilant Attribute)	 value: 5300 (unit: V) (range: 4797, 10000)\n"
                                       "\tspotSize (Vigilant Attribute)	 value: 2.7 (range: 2.1 , 3.3)\n"),
                    model.MD_HW_NAME: "fake hw",
                    model.MD_DESCRIPTION: "green dye",
                    model.MD_ACQ_DATE: time.time() + 2,
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 1),  # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                    model.MD_POS: (13.7e-3, -30e-3),  # m
                    model.MD_EXP_TIME: 1,  # s
                    model.MD_IN_WL: (590e-9, 620e-9),  # m
                    model.MD_OUT_WL: (620e-9, 650e-9),  # m
                    model.MD_ROTATION: 0.1,  # rad
                    model.MD_SHEAR: 0,
                    model.MD_BASELINE: 400.0
                    },
                    {
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
                    {
                    model.MD_HW_NOTE: ("Component 'EBeam Phenom':\n"
                                       "\trole: e-beam\n"
                                       "\taffects: 'BSED Phenom', 'Camera'\n"
                                       "\tshape (RO Attribute)	value: (2048, 2048)\n"
                                       "\tswVersion (RO Attribute)	value: 4.4.2\n"
                                       "\thwVersion (RO Attribute)	value: Phenom G4\n"
                                       "\tscale (Vigilant Attribute)	 value: (4, 4) (range: (0, 0) , (2048, 2048))\n"
                                       "\taccelVoltage (Vigilant Attribute)	 value: 5300 (unit: V) (range: 4797 , 10000)\n"
                                       "\tspotSize (Vigilant Attribute)	 value: 2.7 (range: 2.1 , 3.3)\n"),
                    model.MD_HW_NAME: "fake hw",
                    model.MD_DESCRIPTION: "brightfield",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 1),  # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 1e-6), # m/px
                    model.MD_POS: (13.7e-3, -30e-3), # m
                    model.MD_EXP_TIME: 1.2, # s
                    model.MD_IN_WL: (400e-9, 630e-9), # m
                    model.MD_OUT_WL: (400e-9, 630e-9), # m
                    # correction metadata
                    model.MD_POS_COR: (-1e-6, 3e-6), # m
                    model.MD_PIXEL_SIZE_COR: (1.2, 1.2),
                    model.MD_ROTATION_COR: 6.27,  # rad
                    model.MD_SHEAR_COR: 0.005
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

            if model.MD_HW_NOTE in md:
                self.assertEqual(im.metadata[model.MD_HW_NOTE], md[model.MD_HW_NOTE])
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
            if model.MD_LIGHT_POWER in md and model.MD_LIGHT_POWER in im.metadata:
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
            if eowl[0] <= owl[0] and owl[1] <= eowl[-1]:
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
                     model.MD_INTEGRATION_COUNT: 1,
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
                     model.MD_INTEGRATION_COUNT: 1,
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
                     model.MD_INTEGRATION_COUNT: 1,
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

    def test_uint32(self):
        """
        Checks that can both write and read back image in uint32
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "my exported image",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_DIMS: "YX",
                    },
                    ]
        shape = (5120, 2560)
        dtype = numpy.uint32
        ldata = []
        for i, md in enumerate(metadata):
            a = model.DataArray(numpy.zeros(shape, dtype), md.copy())
            a[...] = 2**24
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
            self.assertEqual(im.shape, shape)
            self.assertEqual(im.dtype, dtype)
            emd = metadata[i].copy()
            rmd = im.metadata
            img.mergeMetadata(emd)
            img.mergeMetadata(rmd)
            self.assertEqual(rmd[model.MD_DESCRIPTION], emd[model.MD_DESCRIPTION])
            self.assertEqual(rmd[model.MD_DIMS], emd[model.MD_DIMS])

    def testReadAndSaveTemporal(self):
        """
        Checks that can both write and read back an time-correlator data
        """
        shape = [(512,340), (1, 1024, 1, 2, 2)]
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "survey",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_DIMS: "YX",
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "my exported image",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_DIMS: "CTZYX",
                     #model.MD_TIME_LIST: [1e-6 * i for i in range(shape[1][1])],
                     model.MD_PIXEL_DUR: 1e-6,
                     model.MD_TIME_OFFSET: 0,
                    },
                    ]
        dtype = numpy.uint32
        ldata = []
        for i, (s, md) in enumerate(zip(shape, metadata)):
            a = model.DataArray(numpy.zeros(s, dtype), md.copy())
            a[...] = 2**24
            a[..., i, i] = i  # "watermark" it
            ldata.append(a)

        # export
        tiff.export(FILENAME, ldata)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            self.assertEqual(im.shape, shape[i])
            self.assertEqual(im.dtype, dtype)
            emd = metadata[i].copy()
            rmd = im.metadata
            img.mergeMetadata(emd)
            img.mergeMetadata(rmd)
            self.assertEqual(rmd[model.MD_DESCRIPTION], emd[model.MD_DESCRIPTION])
            self.assertEqual(rmd[model.MD_DIMS], emd[model.MD_DIMS])

    def testBadTIFFMD(self):
        """
        Checks that can both write and read back data with a negative MD_POS.
        """
        shape = [(512,340)]
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "survey",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (-13.7, -30e-3),  # m # NEGATIVE position
                     model.MD_DIMS: "YX",
                    },
                    ]
        dtype = numpy.uint32
        ldata = []
        for i, (s, md) in enumerate(zip(shape, metadata)):
            a = model.DataArray(numpy.zeros(s, dtype), md.copy())
            a[...] = 2**24
            a[..., i, i] = i  # "watermark" it
            ldata.append(a)

        # export
        tiff.export(FILENAME, ldata)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            self.assertEqual(im.shape, shape[i])
            self.assertEqual(im.dtype, dtype)
            emd = metadata[i].copy()
            rmd = im.metadata
            img.mergeMetadata(emd)
            img.mergeMetadata(rmd)
            self.assertEqual(rmd[model.MD_DESCRIPTION], emd[model.MD_DESCRIPTION])
            self.assertEqual(rmd[model.MD_DIMS], emd[model.MD_DIMS])
            self.assertEqual(rmd[model.MD_POS], emd[model.MD_POS])

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
                     model.MD_OUT_WL: b"pass-through",  # check if it still works if metadata is bytes
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

            # only MD_TIME_LIST is read back
            if model.MD_PIXEL_DUR in md and model.MD_TIME_OFFSET in md:
                self.assertIn(model.MD_TIME_LIST, im.metadata)
                self.assertEqual(len(im.metadata[model.MD_TIME_LIST]), rdata[i].shape[1])

        # check thumbnail
        rthumbs = tiff.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    def testExportSmallPyramid(self):
        """
        Checks that can both write and read back an pyramidal grayscale 16 bit image
        """
        size = (257, 295)
        dtype = numpy.uint16
        arr = numpy.arange(size[0] * size[1]).reshape(size[::-1]).astype(dtype)
        data = model.DataArray(arr)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        im = libtiff.TIFF.open(FILENAME)
        # get an array of offsets, one for each subimage
        sub_ifds = im.GetField(T.TIFFTAG_SUBIFD)
        # check that there is one resized image
        self.assertEqual(len(sub_ifds), 1)

        full_image = im.read_image()
        self.assertEqual(full_image.shape, size[::-1])
        # checking the values in the corner of the tile
        self.assertEqual(full_image[0][0], 0)
        self.assertEqual(full_image[0][-1], 256)
        self.assertEqual(full_image[-1][0], 10022)
        self.assertEqual(full_image[-1][-1], 10278)

        # set the offset of the current subimage
        im.SetSubDirectory(sub_ifds[0])
        # read the subimage
        subimage = im.read_image()
        self.assertEqual(subimage.shape, (147, 128))
        # Checking the values in the corner of the tile. The downsampling uses
        # the neighbour pixels to calculate a pixel in the resized image.
        self.assertEqual(subimage[0][0], 130)
        self.assertEqual(subimage[0][-1], 385)
        self.assertEqual(subimage[-1][0], 9893)
        self.assertEqual(subimage[-1][-1], 10148)

    def testExportThinPyramid(self):           
        """
        Checks that can both write and read back a thin pyramidal grayscale 16 bit image
        """
        size = (2, 2049)
        arr = numpy.arange(size[0] * size[1], dtype=numpy.uint16).reshape(size[::-1])
        data = model.DataArray(arr)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        im = libtiff.TIFF.open(FILENAME)
        # get an array of offsets, one for each subimage
        sub_ifds = im.GetField(T.TIFFTAG_SUBIFD)
        # check that there is only one zoom level, the original image
        self.assertIsNone(sub_ifds)

        full_image = im.read_image()
        assert full_image.shape == size[::-1], repr(full_image.shape)
        # checking the values in the corner of the tile
        self.assertEqual(full_image[0][0], 0)
        self.assertEqual(full_image[0][-1], 1)
        self.assertEqual(full_image[-1][0], 4096)
        self.assertEqual(full_image[-1][-1], 4097)

    def testExportMultiArrayPyramid(self):
        """
        Checks that we can export and read back the metadata and data of 1 SEM image,
        2 optical images, 1 RGB imagem and a RGB thumnail
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "brightfield",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "blue dye",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_BPP: 12,
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "green dye",
                     model.MD_ACQ_DATE: time.time() + 2,
                     model.MD_BPP: 12,
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "green dye",
                     model.MD_ACQ_DATE: time.time() + 2,
                     model.MD_BPP: 12,
                     model.MD_DIMS: "YXC",
                     # In order to test shear is applied even without rotation
                     # provided. And also check that *_COR is merged into its
                     # normal metadata brother.
                     # model.MD_SHEAR: 0.03,
                     model.MD_SHEAR_COR: 0.003,
                    },
                    ]
        # create 3 greyscale images of same size
        size = (5120, 7680)
        dtype = numpy.dtype("uint16")
        ldata = []
        # iterate on the first 3 metadata items
        for i, md in enumerate(metadata[:-1]):
            nparray = numpy.zeros(size[::-1], dtype)
            a = model.DataArray(nparray, md.copy())
            a[i, i + 10] = i # "watermark" it
            ldata.append(a)

        # write a RGB image
        a = model.DataArray(numpy.zeros((514, 516, 3), dtype), metadata[3].copy())
        a[8:24, 24:40] = [5, 8, 13] # "watermark" a square
        ldata.append(a)

        # thumbnail : small RGB completely green
        tshape = (size[1] // 8, size[0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail.metadata[model.MD_DIMS] = "YXC"
        thumbnail.metadata[model.MD_POS] = (13.7e-3, -30e-3)
        thumbnail[:, :, 1] += 255 # green

        # export
        tiff.export(FILENAME, ldata, thumbnail, pyramid=True)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        f = libtiff.TIFF.open(FILENAME)

        # read all images and subimages and store in main_images
        main_images = []
        count = 0
        for im in f.iter_images():
            zoom_level_images = [im]
            # get an array of offsets, one for each subimage
            sub_ifds = f.GetField(T.TIFFTAG_SUBIFD)
            if not sub_ifds:
                main_images.append(zoom_level_images)
                f.SetDirectory(count)
                count += 1
                continue

            for sifd in sub_ifds:
                # set the offset of the current subimage
                f.SetSubDirectory(sifd)
                # read the subimage
                subim = f.read_image()
                zoom_level_images.append(subim)

            f.SetDirectory(count)
            count += 1

            main_images.append(zoom_level_images)

        # check the total number of main images
        self.assertEqual(len(main_images), 5)

        # check the number of zoom level images of the thumbnail
        thumbnail_im = main_images[0]
        self.assertEqual(len(thumbnail_im), 1)
        # thumbnail size
        self.assertEqual(thumbnail_im[0].shape, (960, 640, 3))
        # check the value of one pixel
        self.assertEqual(thumbnail_im[0][0, 0].tolist(), [0, 255, 0])

        # check the sizes of each grayscale pyramidal image
        for main_image in main_images[1:-1]:
            self.assertEqual(len(main_image), 6)
            self.assertEqual(main_image[0].shape, (7680, 5120))
            self.assertEqual(main_image[1].shape, (3840, 2560))
            self.assertEqual(main_image[2].shape, (1920, 1280))
            self.assertEqual(main_image[3].shape, (960, 640))
            self.assertEqual(main_image[4].shape, (480, 320))
            self.assertEqual(main_image[5].shape, (240, 160))

        rgb_image = main_images[4]
        # number of the RGB images
        self.assertEqual(len(rgb_image), 3)
        # size of RGB images with different zoom levels
        self.assertEqual(rgb_image[0].shape, (514, 516, 3))
        self.assertEqual(rgb_image[1].shape, (257, 258, 3))
        self.assertEqual(rgb_image[2].shape, (128, 129, 3))

        # check the watermark on the original image
        self.assertEqual(rgb_image[0][16, 32].tolist(), [5, 8, 13])
        # check the watermark on the resized images
        self.assertEqual(rgb_image[1][8, 16].tolist(), [5, 8, 13])
        self.assertEqual(rgb_image[2][4, 8].tolist(), [5, 8, 13])
        # check the zeroes on some pixels
        self.assertEqual(rgb_image[0][0, 0].tolist(), [0, 0, 0])
        self.assertEqual(rgb_image[1][0, 0].tolist(), [0, 0, 0])
        self.assertEqual(rgb_image[2][0, 0].tolist(), [0, 0, 0])

        # set the directory to the RGB image
        f.SetDirectory(4)
        # get an array of offsets, one for each subimage
        sub_ifds = f.GetField(T.TIFFTAG_SUBIFD)
        # set the subdirectory to the 2nd zoom level
        f.SetSubDirectory(sub_ifds[0])
        # read the top left tile
        tile = f.read_one_tile(0, 0)
        self.assertEqual(tile.shape, (256, 256, 3))
        # check the watermark on the tile
        self.assertEqual(tile[8, 16].tolist(), [5, 8, 13])
        # check the zero on some pixel
        self.assertEqual(tile[0, 0].tolist(), [0, 0, 0])

        # read the bottom right tile
        tile = f.read_one_tile(256, 256)
        # this tile is only 2 x 1 in size
        self.assertEqual(tile.shape, (1, 2, 3))

    def testAcquisitionDataTIFFSmallFile(self):
        num_rows = 10
        num_cols = 5
        size = (num_rows, num_cols)
        md = {
            model.MD_INTEGRATION_COUNT: 1,
            model.MD_DIMS: 'YX',
            model.MD_POS: (2e-6, 10e-6),
            model.MD_PIXEL_SIZE: (1e-6, 1e-6)
        }
        arr = numpy.empty(size, dtype=numpy.uint8)
        data = model.DataArray(arr, metadata=md)
        # export
        tiff.export(FILENAME, data, pyramid=True)
        # check data
        rdata = tiff.open_data(FILENAME)
        self.assertEqual(1, len(rdata.content))
        self.assertEqual(rdata.content[0].maxzoom, 0)
        self.assertEqual(rdata.content[0].shape, size)
        self.assertEqual(rdata.content[0].tile_shape, (256, 256))
        # get the only tile of the image
        tile = rdata.content[0].getTile(0, 0, 0)
        # the tile must have the same shape of the full image
        self.assertEqual(num_rows, len(tile))
        self.assertEqual(num_cols, len(tile[0]))

    def testAcquisitionDataTIFF(self):

        def getSubData(dast, zoom, rect):
            x1, y1, x2, y2 = rect
            tiles = []
            for x in range(x1, x2 + 1):
                tiles_column = []
                for y in range(y1, y2 + 1):
                    tiles_column.append(dast.getTile(x, y, zoom))
                tiles.append(tiles_column)
            return tiles

        size = (3, 257, 295)
        md = {
            model.MD_INTEGRATION_COUNT: 3,
            model.MD_DIMS: 'YXC',
            model.MD_POS: (2e-6, 10e-6),
            model.MD_PIXEL_SIZE: (1e-6, 1e-6)
        }
        arr = numpy.arange(size[0] * size[1] * size[2], dtype=numpy.uint16).reshape(size[::-1])
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        # check data
        rdata = tiff.open_data(FILENAME)
        self.assertEqual(rdata.content[0].maxzoom, 1)
        self.assertEqual(rdata.content[0].shape, size[::-1])

        tiles = getSubData(rdata.content[0], 0, (0, 0, 1, 1))
        self.assertEqual(len(tiles), 2)
        self.assertEqual(len(tiles[0]), 2)
        self.assertEqual(tiles[1][1].shape, (39, 1, 3))

        # Test different zoom levels
        tiles = getSubData(rdata.content[0], 1, (0, 0, 0, 0))
        self.assertEqual(len(tiles), 1)
        self.assertEqual(len(tiles[0]), 1)
        self.assertEqual(tiles[0][0].shape, (147, 128, 3))

        with self.assertRaises(ValueError):
            # invalid Z
            tile = rdata.content[0].getTile(50, 0, 0)

        # save the same file, but not pyramidal this time
        arr = numpy.arange(size[0] * size[1] * size[2], dtype=numpy.uint16).reshape(size[::-1])
        data = model.DataArray(arr, metadata=md)
        tiff.export(FILENAME, data)

        rdata = tiff.open_data(FILENAME)
        with self.assertRaises(AttributeError):
            rdata.content[0].maxzoom

        with self.assertRaises(AttributeError):
            # the image is not tiled
            rdata.content[0].getTile(0, 0, 0)

    def testAcquisitionDataTIFFLargerFile(self):

        def getSubData(dast, zoom, rect):
            x1, y1, x2, y2 = rect
            tiles = []
            for x in range(x1, x2 + 1):
                tiles_column = []
                for y in range(y1, y2 + 1):
                    tiles_column.append(dast.getTile(x, y, zoom))
                tiles.append(tiles_column)
            return tiles

        PIXEL_SIZE = (1e-6, 1e-6)
        ROTATION = 0.3
        SHEAR = 0.2
        size = (6000, 5000)
        md = {
            model.MD_DIMS: 'YX',
            model.MD_POS: (5.0, 7.0),
            model.MD_PIXEL_SIZE: PIXEL_SIZE,
            model.MD_ROTATION: ROTATION,
            model.MD_SHEAR: SHEAR
        }
        arr = numpy.arange(size[0] * size[1], dtype=numpy.uint8).reshape(size[::-1])
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        # check data
        rdata = tiff.open_data(FILENAME)
        self.assertEqual(rdata.content[0].maxzoom, 5)
        self.assertEqual(rdata.content[0].shape, size[::-1])

        # calculate the shapes of each zoomed image
        shapes = tiff._genResizedShapes(rdata.content[0])
        # add the full image to the shape list
        shapes = [rdata.content[0].shape] + shapes
        # TODO shapes unused - add test to check shape is correct

        # First zoom level (full image)
        zoom_level = 0
        # get the top-left tile
        tile_shape = (0, 0, 0, 0)
        tiles = getSubData(rdata.content[0], zoom_level, tile_shape)
        # returns only one tile
        self.assertEqual(len(tiles), 1)
        self.assertEqual(len(tiles[0]), 1)
        tile_md = tiles[0][0].metadata
        exp_pixel_size = (PIXEL_SIZE[0] * 2 ** zoom_level, PIXEL_SIZE[1] * 2 ** zoom_level)
        self.assertEqual(tile_md[model.MD_PIXEL_SIZE], exp_pixel_size)
        self.assertAlmostEqual(tile_md[model.MD_ROTATION], ROTATION)
        self.assertAlmostEqual(tile_md[model.MD_SHEAR], SHEAR)
        numpy.testing.assert_almost_equal(tile_md[model.MD_POS], [4.9963856, 7.001966])
        self.assertEqual(tiles[0][0].shape, (256, 256))

        # get the bottom-right tile
        tile_shape = (6000 // 256, 5000 // 256, 6000 // 256, 5000 // 256)
        tiles = getSubData(rdata.content[0], zoom_level, tile_shape)
        # returns only one tile
        self.assertEqual(len(tiles), 1)
        self.assertEqual(len(tiles[0]), 1)
        tile_md = tiles[0][0].metadata
        self.assertEqual(tile_md[model.MD_PIXEL_SIZE], exp_pixel_size)
        self.assertAlmostEqual(tile_md[model.MD_ROTATION], ROTATION)
        self.assertAlmostEqual(tile_md[model.MD_SHEAR], SHEAR)
        numpy.testing.assert_almost_equal(tile_md[model.MD_POS], [5.0037052, 6.9979841])
        self.assertEqual(tiles[0][0].shape, (136, 112))

        # get all tiles
        tile_shape = (0, 0, 6000 // 256, 5000 // 256)
        tiles = getSubData(rdata.content[0], zoom_level, tile_shape)
        # check the number of tiles in both dimensions
        self.assertEqual(len(tiles), 24)
        self.assertEqual(len(tiles[0]), 20)
        # check the size of the bottom-right tile
        self.assertEqual(tiles[23][19].shape, (136, 112))

        # Zoom level 3
        zoom_level = 3
        # get the top-left tile
        tile_shape = (0, 0, 0, 0)
        tiles = getSubData(rdata.content[0], zoom_level, tile_shape)
        # returns only one tile
        self.assertEqual(len(tiles), 1)
        self.assertEqual(len(tiles[0]), 1)
        tile_md = tiles[0][0].metadata
        exp_pixel_size = (PIXEL_SIZE[0] * 2 ** zoom_level, PIXEL_SIZE[1] * 2 ** zoom_level)
        self.assertEqual(tile_md[model.MD_PIXEL_SIZE], exp_pixel_size)
        self.assertAlmostEqual(tile_md[model.MD_ROTATION], ROTATION)
        self.assertAlmostEqual(tile_md[model.MD_SHEAR], SHEAR)
        numpy.testing.assert_almost_equal(tile_md[model.MD_POS], [4.9975593, 7.0012037])
        self.assertEqual(tiles[0][0].shape, (256, 256))

        # get the bottom-right tile
        tile_shape = (6000 // 256 // 8, 5000 // 256 // 8, 6000 // 256 // 8, 5000 // 256 // 8)
        tiles = getSubData(rdata.content[0], zoom_level, tile_shape)
        # returns only one tile
        self.assertEqual(len(tiles), 1)
        self.assertEqual(len(tiles[0]), 1)
        tile_md = tiles[0][0].metadata
        self.assertEqual(tile_md[model.MD_PIXEL_SIZE], exp_pixel_size)
        self.assertAlmostEqual(tile_md[model.MD_ROTATION], ROTATION)
        self.assertAlmostEqual(tile_md[model.MD_SHEAR], SHEAR)
        numpy.testing.assert_almost_equal(tile_md[model.MD_POS], [5.0026828, 6.9982574])
        self.assertEqual(tiles[0][0].shape, (113, 238))

        # get all tiles
        tile_shape = (0, 0, 6000 // 256 // 8, 5000 // 256 // 8)
        tiles = getSubData(rdata.content[0], zoom_level, tile_shape)
        # check the number of tiles in both dimensions
        self.assertEqual(len(tiles), 3)
        self.assertEqual(len(tiles[0]), 3)
        # check the size of the bottom-right tile
        self.assertEqual(tiles[2][2].shape, (113, 238))

        # Zoom level 5 (max zoom level). The image at this zoom level is smaller than the tile,
        # so there is only one tile in this image
        zoom_level = 5
        # get the top-left tile
        tile_shape = (0, 0, 0, 0)
        tiles = getSubData(rdata.content[0], zoom_level, tile_shape)
        # returns only one tile
        self.assertEqual(len(tiles), 1)
        self.assertEqual(len(tiles[0]), 1)
        tile_md = tiles[0][0].metadata
        exp_pixel_size = (PIXEL_SIZE[0] * 2 ** zoom_level, PIXEL_SIZE[1] * 2 ** zoom_level)
        self.assertEqual(tile_md[model.MD_PIXEL_SIZE], exp_pixel_size)
        self.assertAlmostEqual(tile_md[model.MD_ROTATION], ROTATION)
        self.assertAlmostEqual(tile_md[model.MD_SHEAR], SHEAR)
        numpy.testing.assert_almost_equal(tile_md[model.MD_POS], [4.9999907, 7.000003])
        # the size of this tile is also the size of the image
        self.assertEqual(tiles[0][0].shape, (156, 187))


# Not used anymore
# def rational2float(rational):
#     """
#     Converts a rational number (from libtiff) to a float
#     rational (numpy array of shape 1 with numer and denom fields): num,denom
#     """
#     return rational["numer"][0] / rational["denom"][0]

if __name__ == "__main__":
    unittest.main()
