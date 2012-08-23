#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 20 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import unittest
from odemis.gui.util import units

class TestUnits(unittest.TestCase):


    def test_round_significant(self):
        #         (input) (expected output)
        values = [((1, 1), 1),
                  ((-1.234, 1), -1),
                  ((-1234, 1), -1000),
                  ((1600, 1), 2000),
                  ((-1600, 1), -2000),
                  ((0.0001236, 3), 0.000124),
                  ((0, 5), 0),
                  ]
        for (i, eo) in values:
            o = units.round_significant(*i)
            self.assertEquals(o, eo,
                              "%f to %d figures = %f should be %f" % (i[0], i[1], o, eo))
    
    def test_round_down_significant(self):
        #         (input) (expected output)
        values = [((1, 1), 1),
                  ((-1.234, 1), -1),
                  ((-1234, 1), -1000),
                  ((1600, 1), 1000),
                  ((-1600, 1), -1000),
                  ((0.0001236, 3), 0.000123),
                  ((0, 5), 0),
                  ]
        for (i, eo) in values:
            o = units.round_down_significant(*i)
            self.assertEquals(o, eo,
                              "%f to %d figures = %f should be %f" % (i[0], i[1], o, eo))

    def test_to_string_si_prefix(self):
        #         (input) (expected output)
        values = [((1,), "1 "),
                  ((-1.234,), "-1.234 "),
                  ((-1234,), "-1.234 k"),
                  ((1600,), "1.6 k"),
                  ((-1600,), "-1.6 k"),
                  ((0.0001236,), "123.6 µ"),
                  ((0.0012,), "1.2 m"),
                  ((0,), "0 "),
                  ]
        for (i, eo) in values:
            o = units.to_string_si_prefix(*i)
            self.assertEquals(o, eo,
                              "%f is '%s' while expected '%s'" % (i[0], o, eo))
if __name__ == "__main__":
    unittest.main()
    
    
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: