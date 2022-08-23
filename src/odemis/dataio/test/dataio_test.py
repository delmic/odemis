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
from odemis import dataio
from odemis.dataio import get_available_formats, get_converter, \
    find_fittest_converter
import os
import unittest
from unittest.case import skip


class TestDataIO(unittest.TestCase):

    def test_get_available_formats(self):
        for mode in [os.O_RDONLY, os.O_WRONLY, os.O_RDWR]:
            fmts = get_available_formats(mode)
            self.assertGreaterEqual(len(dataio.__all__), len(fmts))

            for fmt, exts in fmts.items():
                for ext in exts:
                    self.assertTrue(ext.startswith("."),
                            "extension '%s' doesn't start with a dot" % ext)

        # including lossy formats
        all_fmts = get_available_formats(os.O_RDWR, allowlossy=True)
        self.assertEqual(len(dataio.__all__), len(all_fmts) + 3)

    def test_get_converter(self):
        fmts = get_available_formats()
        for fmt in fmts:
            fmt_mng = get_converter(fmt)
            self.assertGreaterEqual(len(fmt_mng.EXTENSIONS), 1)

    def test_find_fittest_converter_write(self):
        # input args -> format name
        test_io = [(("coucou.h5",), "HDF5"),
                   (("coucou.le monde.hdf5",), "HDF5"),
                   (("coucou.H5",), "HDF5"),
                   (("some/fancy/../path/file.tiff",), "TIFF"),
                   (("some/fancy/../.hdf5/h5.ome.tiff",), "TIFF"),
                   (("a/b/d.tiff",), "TIFF"),
                   (("a/b/d.ome.tiff",), "TIFF"),
                   (("a/b/d.OME.tiff",), "TIFF"),
                   (("a/b/d.OME.TIFF",), "TIFF"),
                   (("a/b/d.h5",), "HDF5"),
                   (("a/b/d.b",), "TIFF"), # fallback to tiff
                   (("d.hdf5",), "HDF5"),
                   (("d.HDF5",), "HDF5"),
                   (("a/b/d.0.ome.tiff",), "Serialized TIFF"),
                   (("a/b/d.0.ome.TIFF",), "Serialized TIFF"),
                   ((u"a/b/𝔸𝔹ℂ.ome.tiff".encode("utf-8"),), "TIFF"),  # non-ascii characters
                   ]
        for args, fmt_exp in test_io:
            fmt_mng = find_fittest_converter(*args)
            self.assertEqual(fmt_mng.FORMAT, fmt_exp,
                   "For '%s', expected format %s but got %s" % (args[0], fmt_exp, fmt_mng.FORMAT))

    def test_find_fittest_converter_read(self):
        # input args -> format name
        test_io = [(("coucou.h5",), "HDF5"),
                   (("coucou.le monde.hdf5",), "HDF5"),
                   (("coucou.H5",), "HDF5"),
                   (("some/fancy/../path/file.tiff",), "TIFF"),
                   (("some/fancy/../.hdf5/h5.ome.tiff",), "TIFF"),
                   (("catmaids://fafb.catmaid.virtualflybrain.org/?pid=1&sid0=1",), "Catmaid"),
                   (("catmaid://catmaid.neurodata.io/catmaid/",), "Catmaid"),
                   (("CATMAID://catmaid.neurodata.io/catmaid/",), "Catmaid"),
                   (("a/b/d.tiff",), "TIFF"),
                   (("a/b/d.ome.tiff",), "TIFF"),
                   (("a/b/d.OME.tiff",), "TIFF"),
                   (("a/b/d.OME.TIFF",), "TIFF"),
                   (("a/b/d.h5",), "HDF5"),
                   (("a/b/d.b",), "TIFF"),  # fallback to tiff
                   (("d.hdf5",), "HDF5"),
                   (("d.HDF5",), "HDF5"),
                   (("a/b/d.0.ome.tiff",), "TIFF"),  # Serialised TIFF must be opened by TIFF
                   ((u"a/b/𝔸𝔹ℂ.ome.tiff".encode("utf-8"),), "TIFF"),  # non-ascii characters
                   ]
        for args, fmt_exp in test_io:
            fmt_mng = find_fittest_converter(*args, mode=os.O_RDONLY)
            self.assertEqual(fmt_mng.FORMAT, fmt_exp,
                   "For '%s', expected format %s but got %s" % (args[0], fmt_exp, fmt_mng.FORMAT))


if __name__ == "__main__":
    unittest.main()
