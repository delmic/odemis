#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 17 Jul 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
import model
import pickle
import unittest


class TestDataFlow(unittest.TestCase):
    def test_dataarray_pickle(self):
        darray = model.DataArray([[1, 2],[3, 4]], metadata={"a": 1})
        jar = pickle.dumps(darray)
        up_darray = pickle.loads(jar)
        self.assertEqual(darray.data, up_darray.data, "data is different after pickling")
        self.assertEqual(darray.metadata, up_darray.metadata, "metadata is different after pickling")
        self.assertEqual(up_darray.metadata["a"], 1)

if __name__ == "__main__":
    unittest.main()