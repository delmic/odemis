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
        size = (90, 360)
        dtype = numpy.float
        metadata = {model.MD_DESCRIPTION: "Angle-resolved",
                    model.MD_ACQ_TYPE: model.MD_AT_AR}
        data = model.DataArray(numpy.zeros(size, dtype), metadata)
        data[...] = 26.1561
        data[10, 10] = 10

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

        # test intensity value is at correct position
        file = pycsv.reader(open(FILENAME, 'r', newline=''))

        a = numpy.zeros((91, 361))
        index = 0
        for line in file:
            if index == 0:
                a[index] = 0.0
            else:
                a[index] = line
            index += 1
        # test intensity for same px as defined above is also different when reading back
        # (+1 as we add a line for theta/phi MD to the array when exporting)
        self.assertEqual(a[11][11], 10)

    def testExportSpectrum(self):
        """Try simple spectrum export"""
        size = (150,)
        dtype = numpy.uint16
        md = {model.MD_WL_LIST: numpy.linspace(536e-9, 650e-9, size[0]).tolist(),
              model.MD_ACQ_TYPE: model.MD_AT_SPECTRUM,
              model.MD_DIMS: "C"}
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

    def testExportTheta(self):
        """Try simple angle export"""
        size = (153,)
        dtype = numpy.uint16
        md = {model.MD_THETA_LIST: numpy.linspace(-1.5, 1.4, size[0]),
              model.MD_ACQ_TYPE: model.MD_AT_EK,
              model.MD_DIMS: "A"}
        data = model.DataArray(numpy.zeros(size, dtype), md)
        data += 56

        # export
        csv.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 153)
        raised = False
        try:
            pycsv.reader(open(FILENAME, 'rb'))
        except IOError:
            raised = True
        self.assertFalse(raised, 'Failed to read csv file')

    def testExportThetaNoMD(self):
        """Try simple angle export when THETA_LIST is missing"""
        size = (153,)
        dtype = numpy.uint16
        md = {model.MD_ACQ_TYPE: model.MD_AT_EK,
              model.MD_DIMS: "A"}
        data = model.DataArray(numpy.zeros(size, dtype), md)
        data += 56

        # export
        csv.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 153)
        raised = False
        try:
            pycsv.reader(open(FILENAME, 'rb'))
        except IOError:
            raised = True
        self.assertFalse(raised, 'Failed to read csv file')

    def testExportChronogram(self):
        """Try simple chronogram export"""
        size = (150,)
        dtype = numpy.uint16
        md = {model.MD_TIME_LIST: numpy.linspace(536e-9, 650e-9, size[0]).tolist(),
              model.MD_ACQ_TYPE: model.MD_AT_SPECTRUM,
              model.MD_DIMS: "T"}
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
        md = {model.MD_ACQ_TYPE: model.MD_AT_SPECTRUM, model.MD_DIMS: "C"}
        data = model.DataArray(numpy.zeros(size, dtype), md)
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

    def testExportSpectrumLine(self):
        """Try simple spectrum-line export"""
        size = (1340, 6)
        dtype = numpy.float
        md = {model.MD_WL_LIST: numpy.linspace(536e-9, 650e-9, size[1]).tolist(),
              model.MD_PIXEL_SIZE: (None, 4.2e-06),
              model.MD_ACQ_TYPE: model.MD_AT_SPECTRUM,
              model.MD_DIMS: "XC"}
        data = model.DataArray(numpy.zeros(size, dtype), md)

        # export
        csv.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 5)
        raised = False
        try:
            pycsv.reader(open(FILENAME, 'rb'))
        except IOError:
            raised = True
        self.assertFalse(raised, 'Failed to read csv file')

    def testExportSpectrumLineNoWL(self):
        """Try simple spectrum-line export"""
        size = (1340, 6)
        dtype = numpy.float
        md = {model.MD_PIXEL_SIZE: (None, 4.2e-06),
              model.MD_ACQ_TYPE: model.MD_AT_SPECTRUM,
              model.MD_DIMS: "XC"}
        data = model.DataArray(numpy.zeros(size, dtype), md)

        # export
        csv.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 5)
        raised = False
        try:
            pycsv.reader(open(FILENAME, 'rb'))
        except IOError:
            raised = True
        self.assertFalse(raised, 'Failed to read csv file')

    def testExportTemporalSpectrum(self):
        """Try simple temporal spectrum export; note that size is made up and is not specific"""
        size = (150, 340)
        dtype = numpy.uint16

        # test for wavelength
        md = {model.MD_WL_LIST: numpy.linspace(536e-9, 650e-9, size[1]).tolist(),
              model.MD_ACQ_TYPE: model.MD_AT_TEMPSPECTRUM,
              model.MD_DIMS: "TC"}
        data = model.DataArray(numpy.zeros(size, dtype), md)
        data += 56

        # export
        csv.export(FILENAME, data)

        # test for time
        md = {model.MD_TIME_LIST: numpy.linspace(536e-9, 650e-9, size[0]).tolist(),
              model.MD_ACQ_TYPE: model.MD_AT_TEMPSPECTRUM,
              model.MD_DIMS: "TC"}
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

    def testExportEK(self):
        """Try simple angular spectrum export"""
        size = (340, 1024)
        dtype = numpy.float
        md = {model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(size[-1])],
              model.MD_THETA_LIST: numpy.linspace(-1.5, 1.4, size[-2]),
              model.MD_ACQ_TYPE: model.MD_AT_SPECTRUM,
              model.MD_DIMS: "AC"}
        data = model.DataArray(numpy.zeros(size, dtype), md)

        # export
        csv.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 5)
        raised = False
        try:
            pycsv.reader(open(FILENAME, 'rb'))
        except IOError:
            raised = True
        self.assertFalse(raised, 'Failed to read csv file')

    def testExportEKNoMD(self):
        """Try simple angular spectrum export missing WL_LIST and THETA_LIST"""
        size = (340, 1024)
        dtype = numpy.float
        md = {model.MD_ACQ_TYPE: model.MD_AT_SPECTRUM,
              model.MD_DIMS: "AC"}
        data = model.DataArray(numpy.zeros(size, dtype), md)

        # export
        csv.export(FILENAME, data)

        # check it's here
        st = os.stat(FILENAME)  # this test also that the file is created
        self.assertGreater(st.st_size, 5)
        raised = False
        try:
            pycsv.reader(open(FILENAME, 'rb'))
        except IOError:
            raised = True
        self.assertFalse(raised, 'Failed to read csv file')

    def test_unknown_acquisition_type(self):
        """Test that a ValueError is raised for an unknown acquisition type."""
        size = (10, 10)
        md = {model.MD_ACQ_TYPE: "FAKE",
              model.MD_DIMS: "FAKE"}
        data = model.DataArray(numpy.zeros(size), md)
        with self.assertRaises(ValueError):
            csv.export(FILENAME, data)


if __name__ == "__main__":
    unittest.main()
