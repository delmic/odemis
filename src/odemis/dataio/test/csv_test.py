#!/usr/bin/env python
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
from __future__ import division

import logging
import math
import numpy
from odemis import model
from odemis.dataio import csv
import os
import unittest

import csv as pycsv


logging.getLogger().setLevel(logging.DEBUG)

FILENAME = u"test" + csv.EXTENSIONS[0]


class TestCSVIO(unittest.TestCase):
    def tearDown(self):
        # clean up
        try:
            os.remove(FILENAME)
        except Exception:
            pass

    def testExportAR(self):
        """Try simple AR export"""
        size = (101, 401)
        dtype = numpy.float
        metadata = {model.MD_DESCRIPTION: "Angle-resolved"}
        data = model.DataArray(numpy.zeros(size, dtype), metadata)
        data += 26.1561
        data[1:, 0] = numpy.linspace(0, math.pi / 2, data.shape[0] - 1)
        data[0, 1:] = numpy.linspace(0, math.pi * 2, data.shape[1] - 1)

        # export
        csv.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 100)
        raised = False
        try:
            pycsv.reader(open(FILENAME, 'rb'))
        except IOError:
            raised = True
        self.assertFalse(raised, 'Failed to read csv file')

    def testExportSpectrum(self):
        """Try simple spectrum export"""
        size = (150,)
        dtype = numpy.uint16
        md = {model.MD_WL_LIST: numpy.linspace(536e-9, 650e-9, size[0]).tolist()}
        data = model.DataArray(numpy.zeros(size, dtype), md)
        data += 56

        # export
        csv.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 150)
        raised = False
        try:
            pycsv.reader(open(FILENAME, 'rb'))
        except IOError:
            raised = True
        self.assertFalse(raised, 'Failed to read csv file')

    def testExportSpectrumNoWL(self):
        """Try simple spectrum export"""
        size = (10,)
        dtype = numpy.uint16
        data = model.DataArray(numpy.zeros(size, dtype))
        data += 56486

        # export
        csv.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 10)
        raised = False
        try:
            pycsv.reader(open(FILENAME, 'rb'))
        except IOError:
            raised = True
        self.assertFalse(raised, 'Failed to read csv file')


if __name__ == "__main__":
    unittest.main()
