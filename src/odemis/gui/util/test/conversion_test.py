# -*- coding: utf-8 -*-
'''
Created on 7 Feb 2017

@author: Éric Piel

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.gui.util import conversion
import unittest


class TestConversion(unittest.TestCase):

    def test_change_brightness(self):
        # no change
        col = (0.2, 0.5, 1.0, 0.8)
        ncol = conversion.change_brightness(col, 0)
        self.assertEqual(col, ncol)

        # brighten
        col = (0.2, 0.5, 1.0, 0.8)
        ncol = conversion.change_brightness(col, 0.3)
        self.assertTrue(all(n >= o for o, n in zip(col, ncol)))

        # darken
        col = (0.2, 0.5, 1.0)
        ncol = conversion.change_brightness(col, -0.6)
        self.assertTrue(all(n < o for o, n in zip(col, ncol)))

        # full black
        col = (0.2, 0.5, 1.0, 1.0)
        ncol = conversion.change_brightness(col, -1)
        self.assertTrue(ncol, (0, 0, 0, 1))


if __name__ == "__main__":
    unittest.main()

