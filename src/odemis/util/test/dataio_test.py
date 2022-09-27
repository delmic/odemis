#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 7 Dec 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import numpy
from odemis import model
from odemis.acq import stream
from odemis.dataio import tiff
from odemis.util.dataio import data_to_static_streams, open_acquisition, \
    splitext, _split_planes
import time
import unittest


class TestDataIO(unittest.TestCase):

    def test_data_to_stream(self):
        """
        Check data_to_static_streams
        """
        FILENAME = u"test" + tiff.EXTENSIONS[0]

        # Create fake data of flurorescence acquisition
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "sem",
                     model.MD_ACQ_DATE: time.time() - 1,
                     model.MD_BPP: 16,
                     model.MD_PIXEL_SIZE: (1e-7, 1e-7),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_DWELL_TIME: 100e-6,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "brightfield",
                     model.MD_ACQ_DATE: time.time(),
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_IN_WL: (400e-9, 630e-9),  # m
                     model.MD_OUT_WL: (400e-9, 630e-9),  # m
                     # correction metadata
                     model.MD_POS_COR: (-1e-6, 3e-6),  # m
                     model.MD_PIXEL_SIZE_COR: (1.2, 1.2),
                     model.MD_ROTATION_COR: 6.27,  # rad
                     model.MD_SHEAR_COR: 0.005,
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "blue dye",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                     model.MD_OUT_WL: (650e-9, 660e-9, 675e-9, 678e-9, 680e-9),  # m
                     model.MD_USER_TINT: (255, 0, 65),  # purple
                     model.MD_LIGHT_POWER: 100e-3  # W
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
                     model.MD_IN_WL: (600e-9, 620e-9),  # m
                     model.MD_OUT_WL: (620e-9, 650e-9),  # m
                     model.MD_ROTATION: 0.1,  # rad
                     model.MD_SHEAR: 0,
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

        tiff.export(FILENAME, ldata)

        # check data
        rdata = tiff.read_data(FILENAME)
        sts = data_to_static_streams(rdata)
        # There should be 5 streams: 3 fluo + 1 SEM + 1 Brightfield
        fluo = bright = sem = 0
        for s in sts:
            if isinstance(s, stream.StaticFluoStream):
                fluo += 1
            elif isinstance(s, stream.StaticBrightfieldStream):
                bright += 1
            elif isinstance(s, stream.EMStream):
                sem += 1

        self.assertEqual(fluo, 3)
        self.assertEqual(bright, 1)
        self.assertEqual(sem, 1)

    def test_data_to_stream_pyramidal(self):
        """
        Check data_to_static_streams with pyramidal images using DataArrayShadows
        """
        FILENAME = u"test" + tiff.EXTENSIONS[0]

        # Create fake data of flurorescence acquisition
        metadata = [{model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "sem",
                     model.MD_ACQ_DATE: time.time() - 1,
                     model.MD_BPP: 16,
                     model.MD_PIXEL_SIZE: (1e-7, 1e-7),  # m/px
                     model.MD_POS: (1e-3, -30e-3),  # m
                     model.MD_DWELL_TIME: 100e-6,  # s
                     model.MD_LENS_MAG: 1200,  # ratio
                    },
                    {model.MD_SW_VERSION: "1.0-test",
                     model.MD_HW_NAME: "fake hw",
                     model.MD_DESCRIPTION: "blue dye",
                     model.MD_ACQ_DATE: time.time() + 1,
                     model.MD_BPP: 12,
                     model.MD_BINNING: (1, 1),  # px, px
                     model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
                     model.MD_POS: (13.7e-3, -30e-3),  # m
                     model.MD_EXP_TIME: 1.2,  # s
                     model.MD_IN_WL: (500e-9, 520e-9),  # m
                     model.MD_OUT_WL: (650e-9, 660e-9, 675e-9, 678e-9, 680e-9),  # m
                     model.MD_USER_TINT: (255, 0, 65),  # purple
                     model.MD_LIGHT_POWER: 100e-3  # W
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
                     model.MD_IN_WL: (600e-9, 620e-9),  # m
                     model.MD_OUT_WL: (620e-9, 650e-9),  # m
                     model.MD_ROTATION: 0.1,  # rad
                     model.MD_SHEAR: 0,
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

        tiff.export(FILENAME, ldata, pyramid=True)

        # check data
        rdata = open_acquisition(FILENAME)
        sts = data_to_static_streams(rdata)
        # There should be 3 streams: 2 fluo + 1 SEM
        fluo = sem = 0
        for s in sts:
            if isinstance(s, stream.StaticFluoStream):
                fluo += 1
            elif isinstance(s, stream.EMStream):
                sem += 1

        self.assertEqual(fluo, 2)
        self.assertEqual(sem, 1)

    def test_splitext(self):
        # input, output
        tio = (
            ("/home/test/booo.tiff.png", ("/home/test/booo.tiff", ".png")),
            (".test.doc", (".test", ".doc")),
            ("test.ome.tiff", ("test", ".ome.tiff")),
            ("ahhh....ome.tiff", ("ahhh...", ".ome.tiff")),
            (".bashrc", (".bashrc", "")),
        )

        for inp, eo in tio:
            ao = splitext(inp)
            self.assertEqual(ao, eo, "Unexpected output for '%s': %s" % (inp, ao))


class TestSplitPlanes(unittest.TestCase):

    @classmethod
    def setUpClass(cls) :
        cls.metadata = {model.MD_SW_VERSION: "1.0-test",
                        model.MD_HW_NAME: "fake hw",
                        model.MD_DESCRIPTION: "sem"}

    def _input_image_2d(self):
        md = self.metadata.copy()
        md[model.MD_DIMS] = "XY"
        return model.DataArray(numpy.zeros((512, 512), dtype=numpy.dtype("uint16")), md)

    def _input_image_3d(self):
        return model.DataArray(numpy.zeros((3, 8, 10, 256, 512), dtype=numpy.dtype("uint16")), self.metadata.copy())

    def test_no_x_or_y_planes(self):
        """Test that for a 2d and a 1d DataArray that does not contain an X or Y dim the data is returned unchanged."""
        # Test that the data remains unchanged for a 2d-array, that does not contain an X or Y dimension.
        two_dim_da = self._input_image_2d()
        two_dim_da.metadata[model.MD_DIMS] = "CT"
        da = _split_planes(two_dim_da)[0]
        numpy.testing.assert_array_equal(da, two_dim_da)

        # Check that the metadata remains unchanged
        for k, v in self.metadata.items():
            self.assertEqual(self.metadata[k], da.metadata[k])
            self.assertEqual(self.metadata[k], two_dim_da.metadata[k])

        # Test that the data remains unchanged for a 1d-array, that does not contain an X or Y dimension.
        one_dim_da = model.DataArray(numpy.zeros(512, dtype=numpy.dtype("uint16")), self.metadata.copy())
        one_dim_da.metadata[model.MD_DIMS] = "C"
        da = _split_planes(one_dim_da)[0]
        numpy.testing.assert_array_equal(da, one_dim_da)

        # Check that the metadata remains unchanged
        for k, v in self.metadata.items():
            self.assertEqual(self.metadata[k], da.metadata[k])
            self.assertEqual(self.metadata[k], one_dim_da.metadata[k])

    def test_2d_plane(self):
        """Test that for a 2d DataArray the data is returned unchanged."""
        two_dim_da = self._input_image_2d()
        da = _split_planes(two_dim_da)[0]
        self.assertIsInstance(da, model.DataArray)
        self.assertEqual(da.shape, (512, 512))

        # Check that the metadata remains unchanged
        for k, v in self.metadata.items():
            self.assertEqual(self.metadata[k], da.metadata[k])
            self.assertEqual(self.metadata[k], two_dim_da.metadata[k])

    def test_multi_dim_pLane_yx(self):
        """
        Test that for an array with more than 2 dimensions and the x- and y-axes ordered as YX,
        the input remains unchanged, and the output is correct.
        """
        multi_dim_da = self._input_image_3d()
        multi_dim_da.metadata[model.MD_DIMS] = "CTZYX"
        list_da = _split_planes(multi_dim_da)
        self.assertIsInstance(list_da, list)
        self.assertEqual(multi_dim_da.metadata[model.MD_DIMS], "CTZYX")

        # Check that the metadata remains unchanged
        for k, v in self.metadata.items():
            self.assertEqual(self.metadata[k], multi_dim_da.metadata[k])
            self.assertEqual(self.metadata[k], list_da[0].metadata[k])

        for da in list_da:
            self.assertIsInstance(da, model.DataArray)
            self.assertEqual(da.shape, (256, 512))
            self.assertEqual(da.metadata[model.MD_DIMS], "YX")

    def test_multi_dim_pLane_xy(self):
        """
        Test that for an array with more than 2 dimensions and the x- and y-axes ordered as XY,
        the input remains unchanged, and the output is correct.
        """
        multi_dim_da = self._input_image_3d()
        multi_dim_da.metadata[model.MD_DIMS] = "CTZXY"
        list_da = _split_planes(multi_dim_da)
        self.assertIsInstance(list_da, list)
        self.assertEqual(multi_dim_da.metadata[model.MD_DIMS], "CTZXY")

        # Check that the metadata remains unchanged
        for k, v in self.metadata.items():
            self.assertEqual(self.metadata[k], multi_dim_da.metadata[k])
            self.assertEqual(self.metadata[k], list_da[0].metadata[k])

        for da in list_da:
            self.assertIsInstance(da, model.DataArray)
            self.assertEqual(da.shape, (256, 512))
            self.assertEqual(da.metadata[model.MD_DIMS], "XY")


if __name__ == "__main__":
    unittest.main()
