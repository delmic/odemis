#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 15 Apr 2016

@author: Kimon Tsitsikas

Copyright © 2016 Kimon Tsitsikas, Delmic

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
from PIL import Image
import numpy
from odemis import model
from odemis.dataio import png
import os
import unittest


logging.getLogger().setLevel(logging.DEBUG)

FILENAME = u"test" + png.EXTENSIONS[0]


class TestPNGIO(unittest.TestCase):
    def tearDown(self):
        # clean up
        try:
            os.remove(FILENAME)
        except Exception:
            pass

    def testExportGreyscale(self):
        """Try simple greyscale export"""
        size = (10, 10)
        dtype = numpy.uint16
        data = model.DataArray(numpy.zeros(size, dtype))

        # export
        png.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "PNG")
        self.assertEqual(im.size, size)

    def testExportRGB(self):
        """Try simple RGB export"""
        size = (10, 10, 3)
        dtype = numpy.uint8
        metadata = {model.MD_DIMS: "YXC"}
        data = model.DataArray(numpy.zeros(size, dtype), metadata)

        # export
        png.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "PNG")
        self.assertEqual(im.size, size[:2])

if __name__ == "__main__":
    unittest.main()
