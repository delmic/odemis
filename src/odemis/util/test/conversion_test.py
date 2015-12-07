#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 1 Jul 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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

from odemis import model
from odemis.util import conversion
from odemis.util.conversion import convert_to_object, reproduce_typed_value
import unittest


class TestConversion(unittest.TestCase):

    def test_wave2rgb(self):
        #         (input) (expected output)
        values = [(200.51513e-9, (255, 0, 255)),
                  (350e-9, (255, 0, 255)),
                  (490e-9, (0, 255, 255)),
                  (700e-9, (255, 0, 0)),
                  (900.5e-9, (255, 0, 0)),
                  ]
        for (i, eo) in values:
            o = conversion.wave2rgb(i)
            self.assertEquals(o, eo, u"%f nm -> %s should be %s" % (i * 1e9, o, eo))

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

    def test_convertToObject_good(self):
        """
        check various inputs and compare to expected output
        for values that should work
        """
        # example value / input str / expected output
        tc = [("-1561", -1561),
              ("0.123", 0.123),
              ("true", True),
              ("c: 6,d: 1.3", {"c": 6., "d":1.3}),
              ("-9, -8", [-9, -8]),
              (" 9, -8", [9, -8]),
              ("0, -8, -15.e-3, 6.", [0, -8, -15e-3, 6.0]),
              ("0.1", 0.1),
              ("[aa,bb]", ["aa", "bb"]),
              # TODO: more complicated but nice to support for the user
              # ("256 x 256 px", (256, 256)),
              # ("21 x 0.2 m", (21, 0.2)),
              ("", None),
              ("{5: }", {5: None}), # Should it fail?
              ("-1, 63, 12", [-1, 63, 12]), # NotifyingList becomes a list
              ("9.3, -8", [9.3, -8]),
              # Note: we don't support SI prefixes
              ("[aa, c a]", ["aa", "c a"]),
              ]

        for str_val, expo in tc:
            out = convert_to_object(str_val)
            self.assertEqual(out, expo,
                 "Testing with '%s' -> %s" % (str_val, out))

    def test_convertToObject_bad(self):
        """
        check various inputs and compare to expected output
        for values that should raise an exception
        """
        # example value / input str
        tc = [("{5:"),
              ("[5.3"),
              # ("5,6]"), # TODO
              ]

        for str_val in tc:
            with self.assertRaises((ValueError, TypeError)):
                out = convert_to_object(str_val)

    def test_reproduceTypedValue_good(self):
        """
        check various inputs and compare to expected output
        for values that should work
        """
        lva = model.ListVA([12, -3])
        # example value / input str / expected output
        tc = [(3, "-1561", -1561),
              (-9.3, "0.123", 0.123),
              (False, "true", True),
              ({"a": 12.5, "b": 3.}, "c:6,d:1.3", {"c": 6., "d":1.3}),
              ((-5, 0, 6), " 9, -8", (9, -8)),  # we don't force to be the same size
              ((1.2, 0.0), "0, -8, -15e-3, 6.", (0.0, -8.0, -15e-3, 6.0)),
              ([1.2, 0.0], "0.1", [0.1]),
              (("cou", "bafd"), "aa,bb", ("aa", "bb")),
              # more complicated but nice to support for the user
              ((1200, 256), "256 x 256 px", (256, 256)),
              ((1.2, 256), " 21 x 0.2 m", (21, 0.2)),
              ([-5, 0, 6], "9,, -8", [9, -8]),
              ((1.2, 0.0), "", tuple()),
              (lva.value, "-1, 63, 12", [-1, 63, 12]),  # NotifyingList becomes a list
              ((-5, 0, 6), "9.3, -8", (9, 3, -8)),  # maybe this shouldn't work?
              # Note: we don't support SI prefixes
              (("cou",), "aa, c a", ("aa", " c a")),  # TODO: need to see if spaces should be kept or trimmed
              ]

        for ex_val, str_val, expo in tc:
            out = reproduce_typed_value(ex_val, str_val)
            self.assertEqual(out, expo,
                 "Testing with %s / '%s' -> %s" % (ex_val, str_val, out))

    def test_reproduceTypedValue_bad(self):
        """
        check various inputs and compare to expected output
        for values that should raise an exception
        """
        # example value / input str
        tc = [(3, "-"),
              (-9, "0.123"),
              (False, "56"),
              ({"a": 12.5, "b": 3.}, "6,1.3"),
              (9.3, "0, 123"),
              ]

        for ex_val, str_val in tc:
            with self.assertRaises((ValueError, TypeError)):
                out = reproduce_typed_value(ex_val, str_val)


if __name__ == "__main__":
    unittest.main()


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
