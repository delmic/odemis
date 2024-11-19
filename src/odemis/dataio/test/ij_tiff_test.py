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
import logging
import operator
import os
import time
import unittest
import xml.etree.ElementTree as ET

import numpy
from odemis import model
from odemis.dataio import ij_tiff, tiff

logging.getLogger().setLevel(logging.DEBUG)

FILENAME = u"test" + ij_tiff.EXTENSIONS[0]
class TestImageJTiffIO(unittest.TestCase):

    def tearDown(self):
        # clean up
        try:
            os.remove(FILENAME)
        except Exception:
            pass

    def testWriteImageJMultiZStackSeries(self):
        """
        Checks the xml information of FM images from multiple channels in Z and time series, such that is compatible
         with ImageJ format.
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
                     model.MD_IN_WL: (500e-9, 522e-9),  # m
                     model.MD_OUT_WL: (400e-9, 450e-9),  # m
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
                     model.MD_IN_WL: (590e-9, 620e-9),  # m
                     model.MD_OUT_WL: (520e-9, 550e-9),  # m
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
                     model.MD_IN_WL: (600e-9, 630e-9),  # m
                     model.MD_OUT_WL: (620e-9, 650e-9),  # m
                    },
                    ]
        # create 3 greyscale images with Z stacks of same size
        # define total number in Z and C
        nb_z = 2
        nb_c = len(metadata)
        nb_t = 2
        size = (300, 400, nb_z, nb_t, 1)  # X, Y, Z, T, C
        dtype = numpy.dtype("uint16")
        ldata = []
        for i, md in enumerate(metadata):
            a = model.DataArray(numpy.zeros(size[::-1], dtype), md.copy())
            a[:, :, :, 0, 0] = i
            a[:, :, :, i*20:i*20+10, i*20:i*20+10] = 1000  # "watermark" it
            ldata.append(a)

        # export
        ij_tiff.export(FILENAME, ldata)

        # The multi-channel z stack must belong to same group
        image_groups = tiff._findImageGroups(ldata)
        self.assertEqual(len(image_groups), 1)

        # check imagej description content
        sorted_groups = sorted(image_groups.items(), key=operator.itemgetter(0))
        imagej_description = tiff.extract_imagej_metadata(sorted_groups[0][1])
        self.checkImageJMetadata([size], nb_c, nb_z, nb_t, imagej_description)

        # In xml information for ImageJ, C=3 channel changes fastest, then Z=2 and lastly T=2, For e.g.
        #             <TiffData IFD="0" FirstC="0" FirstT="0" FirstZ="0" PlaneCount="1" />
        #             <TiffData IFD="1" FirstC="1" FirstT="0" FirstZ="0" PlaneCount="1" />
        #             <TiffData IFD="2" FirstC="2" FirstT="0" FirstZ="0" PlaneCount="1" />
        #             <TiffData IFD="3" FirstC="0" FirstT="0" FirstZ="1" PlaneCount="1" />
        #             <TiffData IFD="4" FirstC="1" FirstT="0" FirstZ="1" PlaneCount="1" />
        #             <TiffData IFD="5" FirstC="2" FirstT="0" FirstZ="1" PlaneCount="1" />
        #             <TiffData IFD="6" FirstC="0" FirstT="1" FirstZ="0" PlaneCount="1" />
        #             <TiffData IFD="7" FirstC="1" FirstT="1" FirstZ="0" PlaneCount="1" />
        #             <TiffData IFD="8" FirstC="2" FirstT="1" FirstZ="0" PlaneCount="1" />
        ometxt = tiff._convertToOMEMD(ldata)
        root = ET.fromstring(ometxt)
        # Check the content of first 6 ifds
        ifd_max = 9
        combinations_zc = []
        for i in range(nb_z):
            for j in range(nb_c):
                for k in range(nb_t):
                    combinations_zc.append((i, j, k))

        tiff_data_elements = root.findall('.//{http://www.openmicroscopy.org/Schemas/OME/2016-06}TiffData')
        for ind, element in enumerate(tiff_data_elements):
            tiffdata = element.attrib
            if int(tiffdata["IFD"]) < ifd_max:
                self.assertTrue((int(tiffdata["FirstZ"]), int(tiffdata["FirstC"]), int(tiffdata["FirstT"])) in combinations_zc)
                self.assertEqual(int(tiffdata["IFD"]), ind)
                self.assertEqual(int(tiffdata["PlaneCount"]), 1)
            else:
                break

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)

        # check data
        rdata = tiff.read_data(FILENAME)
        self.assertEqual(len(rdata), len(ldata))

        for i, im in enumerate(rdata):
            shape = ldata[i].shape
            # Pad the shape with 1s to always get 5 dimensions
            res = (1,) * (5 - len(shape)) + shape
            self.assertEqual(im.shape, res)

        # validate the metadata after re-opening the file
        exclude_keys = [model.MD_SW_VERSION, model.MD_ACQ_DATE] # md that is not saved in the file
        # note: acq_date causes timezone issues when comparing, so skipped in this test
        for i, md in enumerate(metadata):
            for key, value in md.items():
                if key in exclude_keys:
                    continue
                self.assertIn(key, rdata[i].metadata)

                if isinstance(value, (tuple, list)):
                    numpy.testing.assert_array_almost_equal(value, rdata[i].metadata[key])
                elif isinstance(value, float):
                    self.assertAlmostEqual(value, rdata[i].metadata[key], delta=1e-5)
                else:
                    self.assertEqual(value, rdata[i].metadata[key])

    def checkImageJMetadata(self, sizes: list, num_channels: int, num_slices: int, num_frames: int, md: dict = None,):
        """
        Extracts ImageJ metadata and checks the values.
        """
        if md is None:
            metadata = {model.MD_SW_VERSION: "1.0-test"}
            ldata = []
            for size in sizes:
                dtype = numpy.uint16
                ldata.append(model.DataArray(numpy.zeros(size, dtype), metadata=metadata))

            md = tiff.extract_imagej_metadata(ldata)

        md_dict = {}
        for line in md.split("\n"):
            if not line:
                continue
            k, v = line.split("=")
            md_dict[k] = v

        self.assertIn('ImageJ', md_dict)
        self.assertEqual(md_dict["images"], str(num_frames*num_slices*num_channels))
        self.assertEqual(md_dict["channels"], str(num_channels))
        self.assertEqual(md_dict["slices"], str(num_slices))
        self.assertEqual(md_dict["frames"], str(num_frames))
        self.assertEqual(md_dict["hyperstack"], "true")
        self.assertEqual(md_dict["unit"], "cm")


    def testImageJMetadata(self):
        """
        Checks the ImageJ metadata for different image shapes.

        Eg: description = "ImageJ=1.11a\nimages={num_slices * num_channels * num_frames}\nchannels={num_channels}
                           f\nslices={num_slices}\nframes=num_frames\nhyperstack=true\nunit=m\n"
        """
        # CTZYX
        sizes = [(1, 2, 256, 512)]
        self.checkImageJMetadata(sizes, num_channels=1, num_frames=1, num_slices=2)

        # With below sizes, first image size is used and rest are dropped
        sizes = [(1, 1, 256, 512),
                 (2, 3, 256, 512),
                 (1, 1, 256, 512)
                 ]
        self.checkImageJMetadata(sizes, num_channels=3, num_frames=1, num_slices=1)

        sizes = [(1, 1, 256, 512),
                 (1, 1, 1, 256, 512),
                 (1, 256, 512)
                 ]
        self.checkImageJMetadata(sizes, num_channels=3, num_frames=1, num_slices=1)

        # First two sizes will be used
        sizes = [(3, 5, 256, 512),
                 (3, 5, 256, 512),
                 (1, 5, 256, 512)
                 ]
        self.checkImageJMetadata(sizes, num_channels=3, num_frames=3, num_slices=5)

        sizes = [(3, 1, 1, 256, 512),
                 (1, 1, 1, 256, 512),
                 (1, 1, 256, 512)
                 ]
        self.checkImageJMetadata(sizes, num_channels=3, num_frames=1, num_slices=1)

        sizes = [(40, 1, 10, 300, 400),
                 (40, 1, 10, 300, 400)
                 ]
        self.checkImageJMetadata(sizes, num_channels=2, num_frames=1, num_slices=10)
