#!/usr/bin/env python
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
from __future__ import division

import numpy
from odemis import model
from odemis.acq import stream
from odemis.dataio import tiff
from odemis.util.dataio import data_to_static_streams, open_acquisition, \
    splitext
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


if __name__ == "__main__":
    unittest.main()

