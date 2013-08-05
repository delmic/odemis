# -*- coding: utf-8 -*-
'''
Created on 1 Aug 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division
from odemis import dataio
from odemis.dataio import get_available_formats, get_exporter, \
    find_fittest_exporter
from unittest.case import skip
import os
import unittest

class TestDataIO(unittest.TestCase):

    def test_get_available_formats(self):
        all_fmts = set()
        for mode in [os.O_RDONLY, os.O_WRONLY, os.O_RDWR]:
            fmts = get_available_formats(mode)
            self.assertGreaterEqual(len(dataio.__all__), len(fmts))
            
            for fmt, exts in fmts.items():
                for ext in exts:
                    self.assertTrue(ext.startswith("."),
                            "extension '%s' doesn't start with a dot" % ext)
            
            all_fmts |= set(fmts)

        self.assertEqual(len(dataio.__all__), len(all_fmts))


    def test_get_exporter(self):
        fmts = get_available_formats()
        for fmt in fmts:
            fmt_mng = get_exporter(fmt)
            self.assertGreaterEqual(fmt_mng.EXTENSIONS, 1)

    def test_find_fittest_exporter(self):
        # input args -> format name
        test_io = [(("coucou.h5",), "HDF5"),
                   (("coucou.le monde.hdf5",), "HDF5"),
                   (("some/fancy/../path/file.tiff",), "TIFF"),
                   (("some/fancy/../.hdf5/h5.ome.tiff",), "TIFF"),
                   ]
        for args, fmt_exp in test_io:
            fmt_mng = find_fittest_exporter(*args)
            self.assertEqual(fmt_mng.FORMAT, fmt_exp,
                   "For '%s', expected format %s but got %s" % (args[0], fmt_exp, fmt_mng.FORMAT))

if __name__ == "__main__":
    unittest.main()
