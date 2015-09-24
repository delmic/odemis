#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 22 Apr 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

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
import logging
import numpy
from numpy.polynomial import polynomial
from odemis import model, dataio
from odemis.dataio import stiff, tiff
from odemis.util import img
import os
import time
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

FILENAME = u"test" + stiff.EXTENSIONS[0]
class TestTiffIO(unittest.TestCase):

    @classmethod
    def setUpClass(self):
        self.no_of_images = 0

    def tearDown(self):
        # clean up
        try:
            tokens = FILENAME.split(".0.", 1)
            for file_index in range(self.no_of_images):
                fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
                os.remove(fname)
        except Exception:
            pass
        
    # @skip("simple")
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
        stiff.export(FILENAME, ldata)
        
        tokens = FILENAME.split(".0.", 1)
        self.no_of_images = 1
        # Iterate through the files generated
        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            # check it's here
            st = os.stat(fname)  # this test also that the file is created
            self.assertGreater(st.st_size, 0)
            im = Image.open(fname)
            self.assertEqual(im.format, "TIFF")

            # check the number of pages
            for i in range(num):
                im.seek(i)
                self.assertEqual(im.size, size)
                self.assertEqual(im.getpixel(white), 124)

        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            os.remove(fname)

    # @skip("simple")
    def testExportOpener(self):
        # create a simple greyscale image
        size = (512, 256)
        white = (12, 52)  # non symmetric position
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            a = model.DataArray(numpy.zeros(size[::-1], dtype))
            a[white[::-1]] = 124
            ldata.append(a)

        # export
        stiff.export(FILENAME, ldata)

        tokens = FILENAME.split(".0.", 1)
        self.no_of_images = 1
        # Iterate through the files generated
        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            
            fmt_mng = dataio.find_fittest_exporter(fname)
            self.assertEqual(fmt_mng.FORMAT, "TIFF",
                   "For '%s', expected format TIFF but got %s" % (fname, fmt_mng.FORMAT))
            rdata = fmt_mng.read_data(fname)
            # Assert all the DAs are there
            self.assertEqual(len(rdata[file_index]), len(ldata))
            
            rthumbnail = fmt_mng.read_thumbnail(fname)
            # No thumbnail handling for now, so assert that is empty
            self.assertEqual(rthumbnail, [])
        
        self.no_of_images = 1
        # Iterate through the files generated
        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            os.remove(fname)

    # @skip("simple")
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
        stiff.export(FILENAME, ldata)

        tokens = FILENAME.split(".0.", 1)
        fname = tokens[0] + "." + str(0) + "." + tokens[1]
        # check it's here
        st = os.stat(fname)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(fname)
        self.assertEqual(im.format, "TIFF")

        # check the 3D data (one image per channel)
        for i in range(size3d[2]):
            im.seek(i)
            self.assertEqual(im.size, size3d[0:2])
            self.assertEqual(im.getpixel((1, 1)), i * step)

        fname = tokens[0] + "." + str(1) + "." + tokens[1]
        # check it's here
        st = os.stat(fname)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(fname)
        self.assertEqual(im.format, "TIFF")
        # check the 2D data
        self.assertEqual(im.size, size)
        self.assertEqual(im.getpixel((1, 1)), 0)

    # @skip("simple")
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
        stiff.export(FILENAME, ldata)

        # check 3D data
        tokens = FILENAME.split(".0.", 1)
        self.no_of_images = 2
        # Iterate through the files generated
        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            # check it's here
            st = os.stat(fname)  # this test also that the file is created
            self.assertGreater(st.st_size, 0)

            rdata = tiff.read_data(fname)
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

    # @skip("simple")
    def testExportRead(self):
        """
        Checks that we can read back an image
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
        stiff.export(FILENAME, ldata, thumbnail)

        tokens = FILENAME.split(".0.", 1)
        # Iterate through the files generated
        for file_index in range(num):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            # check it's here
            st = os.stat(fname)  # this test also that the file is created
            self.assertGreater(st.st_size, 0)

            # check data
            rdata = tiff.read_data(fname)
            self.assertEqual(len(rdata), num)

            for i, im in enumerate(rdata):
                if len(im.shape) > 2:
                    subim = im[0, 0, 0]  # remove C,T,Z dimensions
                else:
                    subim = im  # TODO: should it always be 5 dim?
                self.assertEqual(subim.shape, sizes[i][-1::-1])
                self.assertEqual(subim[white[-1:-3:-1]], ldata[i][white[-1:-3:-1]])

    # @skip("simple")
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
        stiff.export(FILENAME, ldata, thumbnail)

        tokens = FILENAME.split(".0.", 1)
        self.no_of_images = 2
        # Iterate through the files generated
        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            # check it's here
            st = os.stat(fname)  # this test also that the file is created
            self.assertGreater(st.st_size, 0)

            # check data
            rdata = tiff.read_data(fname)
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

    # @skip("simple")
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
                     model.MD_EXP_TIME: 1.2, # s
                     model.MD_LENS_MAG: 1200, # ratio
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
        stiff.export(FILENAME, ldata, thumbnail)

        tokens = FILENAME.split(".0.", 1)
        self.no_of_images = 2
        # Iterate through the files generated
        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            # check it's here
            st = os.stat(fname)  # this test also that the file is created
            self.assertGreater(st.st_size, 0)

            # check data
            rdata = tiff.read_data(fname)
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

    # @skip("simple")
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
                     model.MD_IN_WL: (500e-9, 520e-9), # m
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
                     model.MD_IN_WL: (600e-9, 620e-9), # m
                     model.MD_OUT_WL: (620e-9, 650e-9), # m
                     model.MD_ROTATION: 0.1,  # rad
                     model.MD_SHEAR: 0,
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "red dye",
                     model.MD_ACQ_DATE: time.time() + 2,
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1,  # s
                     model.MD_IN_WL: (600e-9, 620e-9),  # m
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
            a[i, i] = i  # "watermark" it
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (size[1] // 8, size[0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255 # green

        # export
        stiff.export(FILENAME, ldata, thumbnail)

        tokens = FILENAME.split(".0.", 1)
        self.no_of_images = 4
        # Iterate through the files generated
        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            # check it's here
            st = os.stat(fname)  # this test also that the file is created
            self.assertGreater(st.st_size, 0)

            # check data
            rdata = tiff.read_data(fname)
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

                iwl = im.metadata[model.MD_IN_WL]  # nm
                self.assertTrue((md[model.MD_IN_WL][0] <= iwl[0] and
                                 iwl[1] <= md[model.MD_IN_WL][-1]))

                owl = im.metadata[model.MD_OUT_WL]  # nm
                self.assertTrue((md[model.MD_OUT_WL][0] <= owl[0] and
                                 owl[1] <= md[model.MD_OUT_WL][-1]))
                if model.MD_LIGHT_POWER in md:
                    self.assertEqual(im.metadata[model.MD_LIGHT_POWER], md[model.MD_LIGHT_POWER])

                self.assertAlmostEqual(im.metadata.get(model.MD_ROTATION, 0), md.get(model.MD_ROTATION, 0))
                self.assertAlmostEqual(im.metadata.get(model.MD_SHEAR, 0), md.get(model.MD_SHEAR, 0))


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
