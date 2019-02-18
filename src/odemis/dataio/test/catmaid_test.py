#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 19 Feb 2019

@author: Thera Pals

Copyright © 2019 Thera Pals, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
from __future__ import division

import numpy
import unittest
from requests import ConnectionError

from odemis import model
from odemis.dataio import AuthenticationError
from odemis.dataio.catmaid import open_data


class TestCatmaid(unittest.TestCase):

    def test_open_data_neurodata(self):
        """
        Test requesting different tiles from the neurodata server.
        """
        url = "catmaid://catmaid.neurodata.io/catmaid/"
        acquisition = open_data(url)
        acquisition = acquisition.content[0]
        size = (1024, 1024)
        tile = acquisition.getTile(0, 0, 8, depth=437)
        self.assertEqual(tile.shape, size)
        self.assertEqual(tile.max(), 246)
        numpy.testing.assert_almost_equal(tile.metadata[model.MD_POS], [0.000262, -6.55e-05])
        numpy.testing.assert_equal(tile.metadata[model.MD_PIXEL_SIZE], [1024e-9, 1024e-9])
        tile = acquisition.getTile(7, 2, 4, depth=437)
        self.assertEqual(tile.shape, size)
        self.assertEqual(tile.max(), 255)

    def test_open_data_virtualflybrain(self):
        """
        Test requesting different tiles from the virtualflybrain server.
        """
        # test for a url with a specified project id and stack id
        url = 'catmaids://fafb.catmaid.virtualflybrain.org/?pid=2&sid0=2'
        acquisition = open_data(url)
        acquisition = acquisition.content[0]
        size = (1024, 1024)
        tile = acquisition.getTile(6, 2, 4, depth=437)
        self.assertEqual(tile.shape, size)
        self.assertEqual(tile.max(), 250)
        # test that a tile is till returned when the depth is not specified.
        tile = acquisition.getTile(6, 2, 4)
        self.assertEqual(tile.shape, size)
        self.assertEqual(tile.max(), 0)

    @unittest.skip("Requires the Catmaid server to be running locally and the token not to be set.")
    def test_authentication(self):
        """
        Test authentication for the Catmaid server.
        """
        # accessing the stack info with invalid authentication should raise an AuthenticationError
        url = 'catmaid://localhost:8000/?pid=1&sid0=1'
        with self.assertRaises(AuthenticationError):
            open_data(url)
        # accessing the project info with invalid authentication should raise an AuthenticationError
        url = 'catmaid://localhost:8000/'
        with self.assertRaises(AuthenticationError):
            open_data(url)

    def test_non_existing_url(self):
        """
        test that a non existing url raises an error.
        """
        # if the base url does not exist a Connection error is raised.
        url = 'catmaid://catmaid.neurodata.iosdfdfs/catmaid/?pid=1&sid0=1'
        with self.assertRaises(ConnectionError):
            open_data(url)
        # since the instance is hosted at catmaid://catmaid.neurodata.io/catmaid, this url does not contain stack info.
        url = 'catmaid://catmaid.neurodata.io/?pid=1&sid0=1'
        with self.assertRaises(ValueError):
            open_data(url)
        # a ValueError is raised when the pid and sid0 don't exist.
        url = 'catmaid://catmaid.neurodata.io/catmaid/?pid=11&sid0=11'
        with self.assertRaises(ValueError):
            open_data(url)

    def test_non_existing_tile(self):
        """Test that when requesting a non-existing tile, a tile containing only zeros is returned."""
        url = "catmaid://catmaid.neurodata.io/catmaid/"
        acquisition = open_data(url)
        acquisition = acquisition.content[0]
        size = (1024, 1024)
        tile = acquisition.getTile(10000, 100000, 8, depth=437)
        self.assertEqual(tile.shape, size)
        numpy.testing.assert_array_equal(tile, numpy.zeros(size))


if __name__ == '__main__':
    unittest.main()
