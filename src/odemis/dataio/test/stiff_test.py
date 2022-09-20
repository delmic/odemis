#!/usr/bin/env python3
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
from PIL import Image
import logging
import numpy
from odemis import model, dataio
from odemis.dataio import stiff, tiff
from odemis.util import img
import os
import time
import unittest
from unittest.case import skip
import libtiff
import libtiff.libtiff_ctypes as T  # for the constant names

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

    def testExportMultiPage(self):
        # create a simple greyscale image
        size = (512, 256)
        white = (12, 52) # non symmetric position
        dtype = numpy.uint16
        ldata = []
        self.no_of_images = 2
        metadata = [{
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                    },
                    {
                     model.MD_EXP_TIME: 1.2,  # s
                    },
                   ]

        # Add wavelength metadata just to group them
        for i in range(self.no_of_images):
            a = model.DataArray(numpy.zeros(size[::-1], dtype), metadata[i])
            a[white[::-1]] = 124 + i
            ldata.append(a)

        # export
        stiff.export(FILENAME, ldata)

        tokens = FILENAME.split(".0.", 1)
        # Iterate through the files generated
        for i in range(self.no_of_images):
            fname = tokens[0] + "." + str(i) + "." + tokens[1]
            # check it's here
            st = os.stat(fname)  # this test also that the file is created
            self.assertGreater(st.st_size, 0)
            im = Image.open(fname)
            self.assertEqual(im.format, "TIFF")
            self.assertEqual(im.size, size)
            self.assertEqual(im.getpixel(white), 124 + i)
            del im

    def testExportOpener(self):
        # create a simple greyscale image
        size = (512, 256)
        white = (12, 52)  # non symmetric position
        dtype = numpy.uint16
        ldata = []
        self.no_of_images = 2
        metadata = [{
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                    },
                    {
                     model.MD_EXP_TIME: 1.2,  # s
                    },
                   ]

        # Add wavelength metadata just to group them
        for i in range(self.no_of_images):
            a = model.DataArray(numpy.zeros(size[::-1], dtype), metadata[i])
            a[white[::-1]] = 124
            ldata.append(a)

        # export
        stiff.export(FILENAME, ldata)

        tokens = FILENAME.split(".0.", 1)

        # Iterate through the files generated. Opening any of them should be
        # returning _all_ the DAs
        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]

            fmt_mng = dataio.find_fittest_converter(fname, mode=os.O_RDONLY)
            self.assertEqual(fmt_mng.FORMAT, "TIFF",
                   "For '%s', expected format TIFF but got %s" % (fname, fmt_mng.FORMAT))
            rdata = fmt_mng.read_data(fname)
            # Assert all the DAs are there
            self.assertEqual(len(rdata), len(ldata))
            for da in rdata:
                self.assertEqual(da[white[::-1]], 124)

            rthumbnail = fmt_mng.read_thumbnail(fname)
            # No thumbnail handling for now, so assert that is empty
            self.assertEqual(rthumbnail, [])

    def testRename(self):
        """
        Check it's at least possible to open one DataArray, when the files are
        renamed
        """
        # create a simple greyscale image
        size = (512, 256)
        white = (12, 52)  # non symmetric position
        dtype = numpy.uint16
        ldata = []
        self.no_of_images = 2
        metadata = [{
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                     model.MD_EXP_TIME: 0.2, # s
                    },
                    {
                     model.MD_EXP_TIME: 1.2, # s
                    },
                   ]

        # Add metadata
        for i in range(self.no_of_images):
            a = model.DataArray(numpy.zeros(size[::-1], dtype), metadata[i])
            a[white[::-1]] = 124 + i
            ldata.append(a)

        # export
        orig_name = "boo.0.tiff"
        stiff.export(orig_name, ldata)

        tokens = orig_name.split(".0.", 1)
        ntokens = FILENAME.split(".0.", 1)
        # Renaming the file
        for i in range(self.no_of_images):
            fname = tokens[0] + "." + str(i) + "." + tokens[1]
            new_fname = ntokens[0] + "." + str(i) + "." + ntokens[1]
            os.rename(fname, new_fname)

        # Iterate through the new files
        for i in range(self.no_of_images):
            fname = ntokens[0] + "." + str(i) + "." + ntokens[1]

            fmt_mng = dataio.find_fittest_converter(fname, mode=os.O_RDONLY)
            self.assertEqual(fmt_mng.FORMAT, "TIFF",
                   "For '%s', expected format TIFF but got %s" % (fname, fmt_mng.FORMAT))
            rdata = fmt_mng.read_data(fname)
            # Assert that at least one DA is there
            # In practice, currently, we expected precisely 1
            self.assertGreaterEqual(len(rdata), 1)

            # Check the correct metadata is present
            for j in range(self.no_of_images):
                self.assertAlmostEqual(rdata[j].metadata[model.MD_EXP_TIME],
                                       ldata[j].metadata[model.MD_EXP_TIME])

            rthumbnail = fmt_mng.read_thumbnail(fname)
            # No thumbnail handling for now, so assert that is empty
            self.assertEqual(rthumbnail, [])

    def testMissing(self):
        """
        Check it's at least possible to open one DataArray, when the other parts
        are missing.
        """
        # create a simple greyscale image
        size = (512, 256)
        white = (12, 52)  # non symmetric position
        dtype = numpy.uint16
        ldata = []
        self.no_of_images = 2
        metadata = [{
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                     model.MD_EXP_TIME: 0.2,  # s
                    },
                    {
                     model.MD_EXP_TIME: 1.2,  # s
                    },
                   ]

        # Add metadata
        for i in range(self.no_of_images):
            a = model.DataArray(numpy.zeros(size[::-1], dtype), metadata[i])
            a[white[::-1]] = 124 + i
            ldata.append(a)

        # export
        stiff.export(FILENAME, ldata)

        # Ooops, the first file is gone => it should still be possible to open
        # the other files
        os.remove(FILENAME)
        tokens = FILENAME.split(".0.", 1)
        for i in range(1, self.no_of_images):
            fname = tokens[0] + "." + str(i) + "." + tokens[1]

            fmt_mng = dataio.find_fittest_converter(fname, mode=os.O_RDONLY)
            self.assertEqual(fmt_mng.FORMAT, "TIFF",
                   "For '%s', expected format TIFF but got %s" % (fname, fmt_mng.FORMAT))
            rdata = fmt_mng.read_data(fname)
            # Assert that at least one DA is there
            # In practice, currently, we expected precisely 1
            self.assertGreaterEqual(len(rdata), 1)

            # Check the correct metadata is present
            self.assertAlmostEqual(rdata[0].metadata[model.MD_EXP_TIME],
                                   ldata[i].metadata[model.MD_EXP_TIME])

            rthumbnail = fmt_mng.read_thumbnail(fname)
            # No thumbnail handling for now, so assert that is empty
            self.assertEqual(rthumbnail, [])

    def testExportCube(self):
        """
        Check it's possible to export a 3D data (typically: 2D area with full
         spectrum for each point)
        """
        dtype = numpy.dtype("uint16")
        size3d = (512, 256, 220) # X, Y, C
        size = (512, 256)
        sizes = [(512, 256), (100, 110, 1, 1, 200)]
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

        del im

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
        sizes = [(512, 256), (500, 400, 1, 1, 220)]  # different sizes to ensure different acquisitions
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
                     model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(sizes[1][-1])],
                     model.MD_POS: (13.7e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1.2, # s
                    },
                    ]
        # create 2 simple greyscale images
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
                if model.MD_LIGHT_POWER in md and model.MD_LIGHT_POWER in im.metadata:
                    self.assertEqual(im.metadata[model.MD_LIGHT_POWER], md[model.MD_LIGHT_POWER])

                self.assertAlmostEqual(im.metadata.get(model.MD_ROTATION, 0), md.get(model.MD_ROTATION, 0))
                self.assertAlmostEqual(im.metadata.get(model.MD_SHEAR, 0), md.get(model.MD_SHEAR, 0))

    def testExportReadPyramidal(self):
        """
        Checks that we can read back a pyramidal image
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
        stiff.export(FILENAME, ldata, thumbnail, pyramid=True)

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
            a[i, i + 10] = i  # "watermark" it
            ldata.append(a)

        # write a RGB image
        a = model.DataArray(numpy.zeros((514, 516, 3), dtype), metadata[3].copy())
        a[8:24, 24:40] = [5, 8, 13]  # "watermark" a square
        ldata.append(a)

        # thumbnail : small RGB completely green
        tshape = (size[1] // 8, size[0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail.metadata[model.MD_DIMS] = "YXC"
        thumbnail.metadata[model.MD_POS] = (13.7e-3, -30e-3)
        thumbnail[:, :, 1] += 255  # green

        # export
        stiff.export(FILENAME, ldata, thumbnail, pyramid=True)

        tokens = FILENAME.split(".0.", 1)
        self.no_of_images = 4
        # Iterate through the files generated
        for file_index in range(self.no_of_images):
            fname = tokens[0] + "." + str(file_index) + "." + tokens[1]
            # check it's here
            st = os.stat(fname)  # this test also that the file is created

            self.assertGreater(st.st_size, 0)
            f = libtiff.TIFF.open(FILENAME)

            # read all images and subimages and store in main_images
            main_images = []
            count = 0
            for im in f.iter_images():
                zoom_level_images = []
                zoom_level_images.append(im)
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
            self.assertEqual(len(main_images), 1)

            # check the sizes of each grayscale pyramidal image
            for main_image in main_images:
                self.assertEqual(len(main_image), 6)
                self.assertEqual(main_image[0].shape, (7680, 5120))
                self.assertEqual(main_image[1].shape, (3840, 2560))
                self.assertEqual(main_image[2].shape, (1920, 1280))
                self.assertEqual(main_image[3].shape, (960, 640))
                self.assertEqual(main_image[4].shape, (480, 320))
                self.assertEqual(main_image[5].shape, (240, 160))


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
