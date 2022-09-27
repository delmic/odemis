# -*- coding: utf-8 -*-
'''
Created on 14 Jan 2013

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

import h5py
import logging
import numpy
from odemis import model
from odemis.acq.stream import POL_POSITIONS, POL_POSITIONS_RESULTS
from odemis.dataio import hdf5
from odemis.util import img
import os
import time
import unittest
from unittest.case import skip
import json


logging.getLogger().setLevel(logging.DEBUG)

FILENAME = u"test" + hdf5.EXTENSIONS[0]


class TestHDF5IO(unittest.TestCase):

    def tearDown(self):
        # clean up
        try:
            pass
            os.remove(FILENAME)
        except Exception:
            pass

    # Be careful: numpy's notation means that the pixel coordinates are Y,X,C
    def testExportOnePage(self):
        # create a simple greyscale image
        size = (256, 512) # (width, height)
        dtype = numpy.uint16
        data = model.DataArray(numpy.zeros(size[::-1], dtype))
        white = (12, 52) # non symmetric position
        # less that 2**15 so that we don't have problem with PIL.getpixel() always returning an signed int
        data[white[::-1]] = 124

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
        hdf5.export(fn, data)

        # check it's here
        st = os.stat(fn) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        f = h5py.File(fn, "r")
        # need to transform to a full numpy.array just to remove the dimensions
        im = numpy.array(f["Acquisition0/ImageData/Image"])
        im.shape = im.shape[3:5]
        self.assertEqual(im.shape, data.shape)
        self.assertEqual(im[white[-1:-3:-1]], data[white[-1:-3:-1]])

        os.remove(fn)

#    @skip("Doesn't work")
    def testExportMultiPage(self):
        # create two greyscale images corresponding to two fluorescence images
        size = (512, 256)
        white = (52, 12) # non symmetric position
        dtype = numpy.uint8
        ldata = []
        num = 2
        for i in range(num):
            md = {model.MD_IN_WL: (300e-6, 400e-6),
                  model.MD_OUT_WL: (500e-6 + i * 100e-6, 550e-6 + i * 100e-6)
                 }
            d = numpy.zeros(size[::-1], dtype)
            a = model.DataArray(d, md)
            a[white] = 124 + i
            ldata.append(a)

        # export
        hdf5.export(FILENAME, ldata)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        f = h5py.File(FILENAME, "r")

        # check the number of channels
        im = numpy.array(f["Acquisition0/ImageData/Image"])
        for i in range(num):
            subim = im[i, 0, 0] # just one channel
            self.assertEqual(subim.shape, size[::-1])
            self.assertEqual(subim[white], 124 + i)

        os.remove(FILENAME)

#    @skip("Doesn't work")
    def testExportThumbnail(self):
        # create 2 simple greyscale images
        size = (512, 256)
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            md = {model.MD_IN_WL: (300e-6, 400e-6),
                  model.MD_OUT_WL: (500e-6 + i * 100e-6, 550e-6 + i * 100e-6)
            }
            d = numpy.zeros(size[::-1], dtype)
            ldata.append(model.DataArray(d, md))

        # thumbnail : small RGB completely red
        tshape = (size[1] // 8, size[0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 0] += 255 # red
        blue = (12, 22) # non symmetric position
        thumbnail[blue[::-1]] = [0, 0, 255]
        thumbnail.metadata[model.MD_POS] = (0.1, -2)
        thumbnail.metadata[model.MD_PIXEL_SIZE] = (13e-6, 13e-6)

        # export
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        f = h5py.File(FILENAME, "r")

        # look for the thumbnail
        im = f["Preview/Image"]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [255, 0, 0])
        self.assertEqual(im[blue[::-1]].tolist(), [0, 0, 255])
        for exp_name, dim in zip("YXC", im.dims):
            self.assertEqual(exp_name, dim.label)

        # check the number of channels
        im = numpy.array(f["Acquisition0/ImageData/Image"])
        for i in range(num):
            subim = im[i, 0, 0] # just one channel
            self.assertEqual(subim.shape, size[::-1])

    def testExportCube(self):
        """
        Check it's possible to export a 3D data (typically: 2D area with full
         spectrum for each point)
        """
        dtype = numpy.uint16
        size3d = (512, 256, 220) # X, Y, C
        size = (512, 256)
        sizes = [(512, 256), (100, 110, 1, 1, 200)]
        metadata3d = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "fake spec",
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
                    model.MD_DESCRIPTION: u"tÉst",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 2), # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    model.MD_DWELL_TIME: 1.2, #s
                    model.MD_IN_WL: (500e-9, 520e-9), #m
                    }
        ldata = []
        # 3D data generation (+ metadata): gradient along the wavelength
        data3d = numpy.empty(size3d[-1::-1], dtype=dtype)
        end = 2 ** metadata3d[model.MD_BPP]
        step = end // size3d[2]
        lin = numpy.arange(0, end, step, dtype=dtype)[:size3d[2]]
        lin.shape = (size3d[2], 1, 1) # to be able to copy it on the first dim
        data3d[:] = lin
        # introduce Time and Z dimension to state the 3rd dim is channel
        data3d = data3d[:, numpy.newaxis, numpy.newaxis, :, :]
        ldata.append(model.DataArray(data3d, metadata3d))

        # an additional 2D data, for the sake of it
        ldata.append(model.DataArray(numpy.zeros(size[-1::-1], dtype), metadata))

        # export
        hdf5.export(FILENAME, ldata)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        f = h5py.File(FILENAME, "r")

        # check the 3D data
        im = f["Acquisition0/ImageData/Image"]
        self.assertEqual(im[1, 0, 0, 1, 1], step)
        self.assertEqual(im.shape, data3d.shape)
        self.assertEqual(im.attrs["CLASS"], b"IMAGE")
        self.assertEqual(im.attrs["IMAGE_SUBCLASS"], b"IMAGE_GRAYSCALE")

        # check basic metadata
        self.assertEqual(im.dims[4].label, "X")
        self.assertEqual(im.dims[0].label, "C")

        # check the 2D data
        im = numpy.array(f["Acquisition1/ImageData/Image"])
        subim = im[0, 0, 0] # just one channel
        self.assertEqual(subim.shape, size[-1::-1])

    def testExportSpatialCube(self):
        """
        Check it's possible to export 3D spatial data
        """
        dtype = numpy.uint16
        size3d = (512, 256, 100)  # X, Y, Z
        size = (512, 256)
        metadata3d = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "fake spec",
                    model.MD_HW_VERSION: "Spec v3",
                    model.MD_DESCRIPTION: "test3d",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 1),  # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5, 2e-5),  # m/px
                    model.MD_POS: (1e-3, -30e-3),  # m
                    model.MD_EXP_TIME: 1.2,  # s
                    model.MD_IN_WL: (500e-9, 520e-9),  # m
                    }
        metadata = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "",  # check empty strings
                    model.MD_HW_NAME: "vÉ",  # check unicode
                    model.MD_DESCRIPTION: "test",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 2),  # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5, 2e-5),  # m/px
                    model.MD_POS: (1e-3, -30e-3),  # m
                    model.MD_DWELL_TIME: 1.2,  # s
                    model.MD_IN_WL: (500e-9, 520e-9),  # m
                    }
        ldata = []
        # 3D data generation (+ metadata): gradient along the Z
        data3d = numpy.empty(size3d[-1::-1], dtype=dtype)
        end = 2 ** metadata3d[model.MD_BPP]
        lin = numpy.linspace(0, end, size3d[2], dtype=dtype)
        lin.shape = (size3d[2], 1, 1)  # to be able to copy it on the first dim
        data3d[:] = lin
        ldata.append(model.DataArray(data3d, metadata3d))

        # an additional 2D data, for the sake of it
        ldata.append(model.DataArray(numpy.zeros(size[-1::-1], dtype), metadata))

        # export
        hdf5.export(FILENAME, ldata)

        # check data by reading it back
        rdata = hdf5.read_data(FILENAME)

        imr = rdata[0]
        if imr.ndim > 3:
            # Check CT dims are 1 and remove them
            for s in imr.shape[:-3]:
                self.assertEqual(s, 1)
            imr.shape = imr.shape[-3:]
        self.assertEqual(imr.shape, size3d[::-1])

        self.assertEqual(imr.metadata[model.MD_DESCRIPTION], metadata3d[model.MD_DESCRIPTION])
        self.assertEqual(imr.metadata[model.MD_POS], metadata3d[model.MD_POS])
        self.assertEqual(imr.metadata[model.MD_PIXEL_SIZE], metadata3d[model.MD_PIXEL_SIZE])
        self.assertEqual(imr.metadata[model.MD_ACQ_DATE], metadata3d[model.MD_ACQ_DATE])
        self.assertEqual(imr.metadata[model.MD_HW_NAME], metadata3d[model.MD_HW_NAME])
        self.assertEqual(imr.metadata[model.MD_HW_VERSION], metadata3d[model.MD_HW_VERSION])

        self.assertEqual(imr[0, 0, 0], 0)
        self.assertEqual(imr[-1, 0, 0], end)

    def testExportRGB(self):
        """
        Check it's possible to export a 3D data (typically: 2D area with full
         spectrum for each point)
        """
        dtype = numpy.uint8
        size = (3, 512, 256) # C, X, Y
        metadata = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "",  # check empty strings
                    model.MD_HW_VERSION: "vÉ",  # check unicode
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BINNING: (1, 2), # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 1e-6), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    model.MD_EXP_TIME: 1.2, # s
                    model.MD_DIMS: "YXC" # RGB as last dim
                    }
        # RGB data generation (+ metadata): funky gradient
        rgb = numpy.empty(size[-1::-1], dtype=dtype)
        rgb[..., 0] = numpy.linspace(0, 256, size[1])
        rgb[..., 1] = numpy.linspace(128, 256, size[1])
        rgb[..., 2] = numpy.linspace(0, 256, size[1])[::-1]
        rgb = model.DataArray(rgb, metadata)

        # export
        hdf5.export(FILENAME, rgb)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), 1)

        im = rdata[0]
        md = im.metadata
        dims = md[model.MD_DIMS] # for RGB, it should always be set
        if dims == "YXC":
            self.assertEqual(im.shape, rgb.shape)
        else:
            self.assertEqual(dims, "CYX")
            self.assertEqual(im.shape, (size[0], size[2], size[1]))

        self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
        self.assertEqual(im.metadata[model.MD_POS], md[model.MD_POS])
        self.assertEqual(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
        self.assertEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE])
        self.assertEqual(im.metadata[model.MD_HW_NAME], md[model.MD_HW_NAME])
        self.assertEqual(im.metadata[model.MD_HW_VERSION], md[model.MD_HW_VERSION])

    def testMetadata(self):
        """
        checks that the metadata is saved with every picture
        """
        size = (512, 256, 1)
        dtype = numpy.dtype("uint16")
        # list instead of tuple for binning because json only uses lists
        extra_md = {"Camera" : {'binning' : ((0, 0), "px")}, u"¤³ß": {'</Image>': '</Image>'},
                    "Fake component": ("parameter", None)}
        exp_extra_md = json.loads(json.dumps(extra_md))  # slightly different for MD_EXTRA_SETTINGS (tuples are converted to lists)

        metadata = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "fake hw",
                    model.MD_DESCRIPTION: u"tÉst",  # non ascii character
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 2), # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    model.MD_EXP_TIME: 1.2, #s
                    model.MD_IN_WL: (500e-9, 520e-9), #m
                    model.MD_EXTRA_SETTINGS: extra_md,
                    }

        data = model.DataArray(numpy.zeros((size[1], size[0]), dtype), metadata=metadata)

        # thumbnail : small RGB completely red
        tshape = (size[1] // 8, size[0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 0] += 255 # red
        blue = (12, 22) # non symmetric position
        thumbnail[blue[-1:-3:-1]] = [0, 0, 255]

        # export
        hdf5.export(FILENAME, data, thumbnail)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        f = h5py.File(FILENAME, "r")

        # check format
        im = f["Acquisition0/ImageData/Image"]
        self.assertEqual(im.attrs["IMAGE_SUBCLASS"], b"IMAGE_GRAYSCALE")

        # check basic metadata
        self.assertEqual(im.dims[4].label, "X")
        yres = im.dims[3][0][()] # second last dimension (Y), first scale, first and only value
        self.assertAlmostEqual(metadata[model.MD_PIXEL_SIZE][1], yres)
        ypos = f["Acquisition0/ImageData/YOffset"][()]
        self.assertAlmostEqual(metadata[model.MD_POS][1], ypos)

        # Check physical metadata
        desc = f["Acquisition0/PhysicalData/Title"][()]
        self.assertEqual(metadata[model.MD_DESCRIPTION], desc)

        iwl = f["Acquisition0/PhysicalData/ExcitationWavelength"][()] # m
        self.assertTrue((metadata[model.MD_IN_WL][0] <= iwl <= metadata[model.MD_IN_WL][1]))

        expt = f["Acquisition0/PhysicalData/IntegrationTime"][()] # s
        self.assertAlmostEqual(metadata[model.MD_EXP_TIME], expt)

        f.close()

        # Try reading the metadata using the hdf5 module
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), 1)
        for im in rdata:
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], metadata[model.MD_DESCRIPTION])
            self.assertEqual(im.metadata[model.MD_POS], metadata[model.MD_POS])
            self.assertEqual(im.metadata[model.MD_PIXEL_SIZE], metadata[model.MD_PIXEL_SIZE])
            self.assertEqual(im.metadata[model.MD_ACQ_DATE], metadata[model.MD_ACQ_DATE])
            self.assertEqual(im.metadata[model.MD_EXP_TIME], metadata[model.MD_EXP_TIME])
            self.assertEqual(im.metadata[model.MD_EXTRA_SETTINGS], exp_extra_md)

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
            a = model.DataArray(numpy.zeros(sizes[i][::-1], dtype))
            a[white[::-1]] = 1027
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 0] += 255 # red
        blue = (12, 22) # non symmetric position
        thumbnail[blue[::-1]] = [0, 0, 255]
        thumbnail.metadata[model.MD_POS] = (0.1, -2)
        thumbnail.metadata[model.MD_PIXEL_SIZE] = (13e-6, 13e-6)

        # export
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), num)

        for i, im in enumerate(rdata):
            subim = im[0, 0, 0] # remove C,T,Z dimensions
            self.assertEqual(subim.shape, sizes[i][-1::-1])
            self.assertEqual(subim[white[-1:-3:-1]], ldata[i][white[-1:-3:-1]])

        # check thumbnail
        rthumbs = hdf5.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [255, 0, 0])
        self.assertEqual(im[blue[::-1]].tolist(), [0, 0, 255])
        self.assertAlmostEqual(im.metadata[model.MD_POS], thumbnail.metadata[model.MD_POS])

    def testReadAndSaveMDSpec(self):
        """
        Checks that we can save and read back the metadata of a spectrum image.
        """
        # TODO: write testcase for WL_LIST and MD_POLYNOMIAL
        # create 2 simple greyscale images (sem overview, Spec): XY, XYZTC (XY ebeam pos scanned, C Spec info)
        sizes = [(512, 256), (100, 110, 1, 1, 200)]  # different sizes to ensure different acquisitions
        # Create fake current over time report
        cot = [[time.time(), 1e-12]]
        for i in range(1, 171):
            cot.append([cot[0][0] + i, i * 1e-12])

        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "test spectrum",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake spec",
                     model.MD_DESCRIPTION: "test3d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1), # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(sizes[1][-1])],
                     model.MD_OUT_WL: "pass-through",
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_EBEAM_CURRENT_TIME: cot,
                    },
                    ]
        # create 2 simple greyscale images
        dtype = numpy.dtype("uint8")
        ldata = []
        for i, s in enumerate(sizes):
            a = model.DataArray(numpy.random.randint(0, 200, s[::-1], dtype), metadata[i])
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255  # green

        # export
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            md = metadata[i]
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertEqual(im.metadata[model.MD_POS], md[model.MD_POS])
            self.assertEqual(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE])
            if model.MD_LENS_MAG in md:
                self.assertEqual(im.metadata[model.MD_LENS_MAG], md[model.MD_LENS_MAG])

            # None of the images are using light => no MD_IN_WL
            self.assertFalse(model.MD_IN_WL in im.metadata,
                             "Reporting excitation wavelength while there is none")

            if model.MD_EBEAM_CURRENT_TIME in md:
                # Note: technically, it could be a list or tuple and still be fine
                # but we know that hdf5 returns a list of list
                cot = md[model.MD_EBEAM_CURRENT_TIME]
                self.assertEqual(im.metadata[model.MD_EBEAM_CURRENT_TIME], cot)

        # check thumbnail
        rthumbs = hdf5.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

# TODO: test compatibility with Hyperspy for loading spectra (exported by Odemis)

    def testReadAndSaveMDTempSpec(self):
        """
        Checks that we can save and read back the metadata of a temporal spectrum image
        """
        # create 2 simple greyscale images (sem overview, tempSpec): XY, XYZTC (XY ebeam pos scanned, TC tempSpec image)
        sizes = [(512, 256), (100, 110, 1, 50, 60)]  # different sizes to ensure different acquisitions
        # Create fake current over time report
        cot = [[time.time(), 1e-12]]
        for i in range(1, 171):
            cot.append([cot[0][0] + i, i * 1e-12])

        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "test temporal spectrum",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                     },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake temp spec",
                     model.MD_DESCRIPTION: "test3d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(sizes[1][-1])],
                     model.MD_TIME_LIST: [1e-9 * i for i in range(sizes[1][-2])],
                     model.MD_STREAK_MCPGAIN: 3,
                     model.MD_STREAK_MODE: True,
                     model.MD_STREAK_TIMERANGE: 0.001,  # sec
                     model.MD_TRIGGER_DELAY: 0.0000001,  # sec
                     model.MD_TRIGGER_RATE: 1000000,  # Hz
                     model.MD_OUT_WL: "pass-through",
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_INTEGRATION_COUNT: 1,
                     model.MD_EBEAM_CURRENT_TIME: cot,
                     },
                    ]
        # create 2 simple greyscale images
        dtype = numpy.dtype("uint8")
        ldata = []
        for i, s in enumerate(sizes):
            a = model.DataArray(numpy.random.randint(0, 200, s[::-1], dtype), metadata[i])
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255  # green

        # export
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            md = metadata[i]
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertEqual(im.metadata[model.MD_POS], md[model.MD_POS])
            self.assertEqual(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE])
            if model.MD_LENS_MAG in md:
                self.assertEqual(im.metadata[model.MD_LENS_MAG], md[model.MD_LENS_MAG])

            # None of the images are using light => no MD_IN_WL
            self.assertFalse(model.MD_IN_WL in im.metadata,
                             "Reporting excitation wavelength while there is none")

            if model.MD_WL_LIST in md:
                wl = md[model.MD_WL_LIST]
                self.assertEqual(im.metadata[model.MD_WL_LIST], wl)
            if model.MD_TIME_LIST in md:
                tm = md[model.MD_TIME_LIST]
                self.assertListEqual(im.metadata[model.MD_TIME_LIST], tm)
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
                # Note: technically, it could be a list or tuple and still be fine
                # but we know that hdf5 returns a list of list
                cot = md[model.MD_EBEAM_CURRENT_TIME]
                self.assertEqual(im.metadata[model.MD_EBEAM_CURRENT_TIME], cot)

        # check thumbnail
        rthumbs = hdf5.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    def testReadAndSaveMDAngularSpec(self):
        """
        Checks that we can save and read back the metadata of an angular spectrum image
        """
        # Creates 2 simple greyscale images (SEM overview, angularSpec): XY, XYZAC (XY ebeam pos scanned,
        # AC anglularSpec image)
        sizes = [(512, 256), (100, 110, 1, 50, 60)]  # different sizes to ensure different acquisitions

        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "test angular spectrum",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                     },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake angular spec",
                     model.MD_DESCRIPTION: "test3d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(sizes[1][-1])],
                     model.MD_THETA_LIST: numpy.linspace(-1.5, 1.4, sizes[1][-2]),
                     model.MD_OUT_WL: "pass-through",
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_INTEGRATION_COUNT: 1,
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_AR_POLE: (253.1, 65.1),
                     model.MD_AR_MIRROR_TOP: (1253.1, 65.1),  # px, px/m
                     model.MD_AR_MIRROR_BOTTOM: (254, -451845.48),  # px, px/m
                     model.MD_AR_XMAX: 12e-3,
                     model.MD_AR_FOCUS_DISTANCE: 0.5e-3,
                     model.MD_AR_PARABOLA_F: 2e-3,
                     },
                    ]
        # create 2 simple greyscale images
        dtype = numpy.dtype("uint8")
        ldata = []
        for i, s in enumerate(sizes):
            a = model.DataArray(numpy.random.randint(0, 200, s[::-1], dtype), metadata[i])
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (sizes[0][1] // 8, sizes[0][0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255  # green

        # export
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            md = metadata[i]
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertEqual(im.metadata[model.MD_POS], md[model.MD_POS])
            self.assertEqual(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE])

            # Check the metadata stays the same, when the metadata was present
            if model.MD_LENS_MAG in md:
                self.assertEqual(im.metadata[model.MD_LENS_MAG], md[model.MD_LENS_MAG])
            if model.MD_AR_POLE in md:
                self.assertEqual(im.metadata[model.MD_AR_POLE], md[model.MD_AR_POLE])
            if model.MD_AR_MIRROR_TOP in md:
                self.assertEqual(im.metadata[model.MD_AR_MIRROR_TOP], md[model.MD_AR_MIRROR_TOP])
            if model.MD_AR_MIRROR_BOTTOM in md:
                self.assertEqual(im.metadata[model.MD_AR_MIRROR_BOTTOM], md[model.MD_AR_MIRROR_BOTTOM])
            if model.MD_AR_XMAX in md:
                self.assertEqual(im.metadata[model.MD_AR_XMAX], md[model.MD_AR_XMAX])
            if model.MD_AR_FOCUS_DISTANCE in md:
                self.assertEqual(im.metadata[model.MD_AR_FOCUS_DISTANCE], md[model.MD_AR_FOCUS_DISTANCE])
            if model.MD_AR_PARABOLA_F in md:
                self.assertEqual(im.metadata[model.MD_AR_PARABOLA_F], md[model.MD_AR_PARABOLA_F])

            # None of the images are using light => no MD_IN_WL
            self.assertFalse(model.MD_IN_WL in im.metadata,
                             "Reporting excitation wavelength while there is none")

            if model.MD_WL_LIST in md:
                wl = md[model.MD_WL_LIST]
                self.assertEqual(im.metadata[model.MD_WL_LIST], wl)
            if model.MD_THETA_LIST in md:
                thetal = md[model.MD_THETA_LIST]
                numpy.testing.assert_almost_equal(im.metadata[model.MD_THETA_LIST], thetal)
            if model.MD_INTEGRATION_COUNT in md:
                self.assertEqual(im.metadata[model.MD_INTEGRATION_COUNT], md[model.MD_INTEGRATION_COUNT])

        # check thumbnail
        rthumbs = hdf5.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    def testReadAndSaveMDAR(self):
        """
        Checks that we can read back the metadata of an Angular Resolved image
        """
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     # TODO why not in checked - see below..
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
                     model.MD_AR_POLE: (253.1, 65.1),
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
                     model.MD_AR_POLE: (253.1, 65.1),
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
        thumbnail[:, :, 1] += 255  # green

        # export
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this tests also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for im, md in zip(rdata, metadata):
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertEqual(im.metadata[model.MD_POS], md[model.MD_POS])
            self.assertEqual(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE])
            if model.MD_AR_POLE in md:
                self.assertEqual(im.metadata[model.MD_AR_POLE], md[model.MD_AR_POLE])
            if model.MD_AR_XMAX in md:
                self.assertEqual(im.metadata[model.MD_AR_XMAX], md[model.MD_AR_XMAX])
            if model.MD_AR_HOLE_DIAMETER in md:
                self.assertEqual(im.metadata[model.MD_AR_HOLE_DIAMETER], md[model.MD_AR_HOLE_DIAMETER])
            if model.MD_AR_FOCUS_DISTANCE in md:
                self.assertEqual(im.metadata[model.MD_AR_FOCUS_DISTANCE], md[model.MD_AR_FOCUS_DISTANCE])
            if model.MD_AR_PARABOLA_F in md:
                self.assertEqual(im.metadata[model.MD_AR_PARABOLA_F], md[model.MD_AR_PARABOLA_F])
            if model.MD_LENS_MAG in md:
                self.assertEqual(im.metadata[model.MD_LENS_MAG], md[model.MD_LENS_MAG])
            if model.MD_EBEAM_CURRENT in md:
                self.assertEqual(im.metadata[model.MD_EBEAM_CURRENT], md[model.MD_EBEAM_CURRENT])
            if model.MD_EBEAM_VOLTAGE in md:
                self.assertEqual(im.metadata[model.MD_EBEAM_VOLTAGE], md[model.MD_EBEAM_VOLTAGE])

        # check thumbnail
        rthumbs = hdf5.read_thumbnail(FILENAME)
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
        sizes = [(512, 256), (500, 400), (500, 400), (500, 400), (500, 400), (500, 400), (500, 400)]
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
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this tests also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for im, md in zip(rdata, metadata):
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertEqual(im.metadata[model.MD_POS], md[model.MD_POS])
            self.assertEqual(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE])
            if model.MD_POL_MODE in md:
                self.assertEqual(im.metadata[model.MD_POL_MODE], md[model.MD_POL_MODE])
            if model.MD_POL_POS_QWP in md:
                self.assertEqual(im.metadata[model.MD_POL_POS_QWP], md[model.MD_POL_POS_QWP])
            if model.MD_POL_POS_LINPOL in md:
                self.assertEqual(im.metadata[model.MD_POL_POS_LINPOL], md[model.MD_POL_POS_LINPOL])

        # check thumbnail
        rthumbs = hdf5.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    def testReadAndSaveMDARPolarimetry(self):
        """
        Checks that we can read back the metadata of polarimetry images (the visualization of angular resolved images
         acquired with an polarization analyzer).
        """
        pol_pos = POL_POSITIONS_RESULTS
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
                     model.MD_DESCRIPTION: "test ar polarimetry",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6),  # m/px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                     model.MD_POS: (1.2e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
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
        sizes = [(512, 256), (500, 400), (500, 400), (500, 400), (500, 400), (500, 400), (500, 400)]
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
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this tests also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for im, md in zip(rdata, metadata):
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertEqual(im.metadata[model.MD_POS], md[model.MD_POS])
            self.assertEqual(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE])
            if model.MD_POL_MODE in md:
                self.assertEqual(im.metadata[model.MD_POL_MODE], md[model.MD_POL_MODE])
            self.assertNotIn(model.MD_POL_POS_QWP, im.metadata)  # should be not in md
            self.assertNotIn(model.MD_POL_POS_QWP, im.metadata)  # should be not in md

        # check thumbnail
        rthumbs = hdf5.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

    def testReadMDFluo(self):
        """
        Checks that we can read back the metadata of a fluoresence image
        The HDF5 file will contain just one big array, but three arrays 
        should be read back with the right data. With the rotation, the
        last array should be kept separate.
        """
        # SVI HDF5 only records one acq time per T dimension
        # so only record and save one time
        acq_date = time.time()

        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "brightfield",
                     model.MD_ACQ_DATE: acq_date,
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
                     model.MD_ROTATION_COR: 6.27, # rad
                     model.MD_SHEAR_COR: 0.01,
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "blue dye",
                     model.MD_ACQ_DATE: acq_date,
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1), # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6), # m/px
                     model.MD_POS: (13.7e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1.2, # s
                     model.MD_IN_WL: (500e-9, 520e-9), # m
                     model.MD_OUT_WL: (650e-9, 660e-9, 675e-9, 680e-9, 686e-9), # m
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "green dye",
                     model.MD_ACQ_DATE: acq_date,
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1), # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6), # m/px
                     model.MD_POS: (13.7e-3, -30e-3), # m
                     model.MD_EXP_TIME: 1, # s
                     model.MD_IN_WL: (600e-9, 620e-9), # m
                     model.MD_OUT_WL: (620e-9, 650e-9), # m
                     model.MD_ROTATION: 0.1, # rad
                     model.MD_SHEAR: 0,
                     model.MD_BASELINE: 200
                    },
                    ]
        # create 3 greyscale images of same size
        size = (512, 256)
        dtype = numpy.dtype("uint16")
        ldata = []
        for i, md in enumerate(metadata):
            a = model.DataArray(numpy.zeros(size[::-1], dtype), md)
            a[i, i] = i # "watermark" it
            ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (size[1] // 8, size[0] // 8, 3)
        tdtype = numpy.uint8
        thumbnail = model.DataArray(numpy.zeros(tshape, tdtype))
        thumbnail[:, :, 1] += 255 # green

        # export
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        # TODO: rdata and ldata don't have to be in the same order
        for i, im in enumerate(rdata):
            md = metadata[i].copy()
            img.mergeMetadata(md)
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertAlmostEqual(im.metadata[model.MD_POS][0], md[model.MD_POS][0])
            self.assertAlmostEqual(im.metadata[model.MD_POS][1], md[model.MD_POS][1])
            self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE][0], md[model.MD_PIXEL_SIZE][0])
            self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE][1], md[model.MD_PIXEL_SIZE][1])

            iwl = im.metadata[model.MD_IN_WL] # nm
            self.assertTrue((md[model.MD_IN_WL][0] <= iwl[0] and
                             iwl[1] <= md[model.MD_IN_WL][-1]))

            owl = im.metadata[model.MD_OUT_WL] # nm
            self.assertTrue((md[model.MD_OUT_WL][0] <= owl[0] and
                             owl[1] <= md[model.MD_OUT_WL][-1]))

            self.assertAlmostEqual(im.metadata[model.MD_ACQ_DATE], acq_date, delta=1)

            # SVI HDF5 doesn't this metadata:
#            self.assertEqual(im.metadata[model.MD_BPP], md[model.MD_BPP])
#            self.assertEqual(im.metadata[model.MD_BINNING], md[model.MD_BINNING])
            self.assertEqual(im.metadata[model.MD_EXP_TIME], md[model.MD_EXP_TIME])
            self.assertEqual(im.metadata.get(model.MD_ROTATION, 0), md.get(model.MD_ROTATION, 0))
            self.assertEqual(im.metadata.get(model.MD_BASELINE, 0), md.get(model.MD_BASELINE, 0))
            self.assertEqual(im.metadata.get(model.MD_SHEAR, 0), md.get(model.MD_SHEAR, 0))
        # check thumbnail
        rthumbs = hdf5.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])

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
                     model.MD_OUT_WL: ((630e-9, 660e-9), (675e-9, 690e-9)),  # m
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
            ldata.append(a)

        # export
        hdf5.export(FILENAME, ldata)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

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
        The HDF5 file will contain just one big array, but two arrays should be
        read back with the right data. We expect the Output wavelength range to
        be read back correctly.
        """
        acq_date = time.time()

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
        hdf5.export(FILENAME, ldata)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
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
        self.assertEqual(owl, ldata[0].metadata[model.MD_OUT_WL])

    def testReadMDTime(self):
        """
        Checks that we can read back the metadata of an acquisition with time correlation
        """
        shapes = [(512, 256), (1, 5220, 1, 50, 40), (1, 65000)]
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "test",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 2),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_DWELL_TIME: 1.2,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake time correlator",
                     model.MD_DESCRIPTION: "test3d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 16,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 2e-6),  # m/px
                     model.MD_TIME_LIST: [1e-9 * i + 20e-9 for i in range(shapes[1][1])],
                     model.MD_OUT_WL: "pass-through",
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_DWELL_TIME: 1.2,  # s
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake time correlator",
                     model.MD_DESCRIPTION: "test1d",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 16,
                     model.MD_BINNING: (1, 128),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_TIME_LIST: [10e-12 * i - 500e-12 for i in range(shapes[2][1])],
                     model.MD_OUT_WL: (500e-9, 600e-9),
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_DWELL_TIME: 1.2,  # s
                     model.MD_DIMS: "XT",
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
        a[0, 10] = 20000
        ldata.append(a)

        # thumbnail : small RGB completely red
        tshape = (400, 300, 3)
        thumbnail = model.DataArray(numpy.zeros(tshape, numpy.uint8))
        thumbnail[:, :, 1] += 255  # green

        # export
        hdf5.export(FILENAME, ldata, thumbnail)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = hdf5.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            orshape = shapes[i]
            if len(orshape) == 5:
                self.assertEqual(orshape, im.shape)
            md = metadata[i]
            self.assertEqual(im.metadata[model.MD_DESCRIPTION], md[model.MD_DESCRIPTION])
            self.assertEqual(im.metadata[model.MD_POS], md[model.MD_POS])
            self.assertEqual(im.metadata[model.MD_PIXEL_SIZE], md[model.MD_PIXEL_SIZE])
            self.assertEqual(im.metadata[model.MD_ACQ_DATE], md[model.MD_ACQ_DATE])
            if model.MD_LENS_MAG in md:
                self.assertEqual(im.metadata[model.MD_LENS_MAG], md[model.MD_LENS_MAG])

            # None of the images are using light => no MD_IN_WL
            self.assertFalse(model.MD_IN_WL in im.metadata,
                             "Reporting excitation wavelength while there is none")

            if model.MD_TIME_LIST in md:
                self.assertIn(model.MD_TIME_LIST, list(im.metadata.keys()))

        # check thumbnail
        rthumbs = hdf5.read_thumbnail(FILENAME)
        self.assertEqual(len(rthumbs), 1)
        im = rthumbs[0]
        self.assertEqual(im.shape, tshape)
        self.assertEqual(im[0, 0].tolist(), [0, 255, 0])


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
