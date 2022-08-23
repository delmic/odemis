#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 20 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
import numpy
from odemis.util import units
import unittest


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
                  ((4.375479375074184e-6, 3), 4.38e-6),
                  ]
        for (i, eo) in values:
            o = units.round_significant(*i)
            self.assertEqual(o, eo,
                              u"%f to %d figures = %f should be %f" % (i[0], i[1], o, eo))

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
            self.assertEqual(o, eo,
                              u"%f to %d figures = %f should be %f" % (i[0], i[1], o, eo))

    def test_to_string_si_prefix(self):
        #         (input) (expected output)
        values = [((1.0,), "1 "),
                  ((-1.234,), "-1.234 "),
                  ((-1234,), "-1.234 k"),
                  ((1600,), "1.6 k"),
                  ((-1600,), "-1.6 k"),
                  ((0.0001236,), u"123.6 µ"),
                  ((0.0012,), "1.2 m"),
                  ((0,), "0 "),
                  ]
        for (i, eo) in values:
            o = units.to_string_si_prefix(*i)
            self.assertEqual(o, eo,
                              u"%f is '%s' while expected '%s'" % (i[0], o, eo))

    def test_readable_str(self):
        #         (input) (expected output)
        values = [((1.0, None), "1"),
                  ((1, None), "1"),
                  ((-1.234, "m"), "-1.234 m"),
                  ((-1234, "g"), "-1.234 kg"),
                  ((160000, None), "160000"),
                  ((16, None), "16"),
                  ((160001, None, 3), "160000"),
                  ((16000000.0, None), "16000000"),
                  ((16.3000000001, "px", 3), "16.3 px"),
                  ((numpy.float64(16.3), "px", 3), "16.3 px"),
                  ((-1600, "N"), "-1.6 kN"),
                  ((-1601, "N", 3), "-1.6 kN"),  # sig=3
                  ((0.0001236, None), "0.0001236"),
                  ((0.0012, "V"), "1.2 mV"),
                  ((999.999, "V", 3), "1 kV"),
                  ((999.5, "V"), "999.5 V"),
                  ((999.5, "V", 3), "1 kV"),
                  ((999.4, "V", 3), "999 V"),
                  ((200e-6, "m"), u"200 µm"),
                  ((0.0, "m"), "0 m"),
                  ((0, "rad"), "0 rad"),
                  ((0, "rad", 3), "0 rad"),
                  (([1500, 1200, 150], None), "1500 x 1200 x 150"),
                  (([0.0001236, 0.00014], "m"), u"123.6 x 140 µm"),
                  (([0.0001236, 12.0], "m"), "0.0001236 x 12 m"),
                  (([1200, 1000], "px"), "1200 x 1000 px"), # special non-prefix unit
                  (([-float("inf"), float("NaN")], "m"), u"-∞ x unknown m"),
                  ]
        for (i, eo) in values:
            o = units.readable_str(*i)
            self.assertEqual(o, eo,
                              u"%s is '%s' while expected '%s'" % (i, o, eo))

    def test_readable_time(self):
        #         (input) (expected output)
        values = [((1.0,), "1 second"),
                  ((0,), "0 second"),
                  ((3601,), "1 hour and 1 second"),
                  ((12.350,), "12 seconds and 350 milliseconds"),
                  ((12.3501,), "12 seconds, 350 milliseconds and 100 microseconds"),
                  ((1e-6,), "1 microsecond"),
                  ((12 + 50e-6,), "12 seconds and 50 microseconds"),
                  ((3 * 24 * 60 * 60 + 12 * 60,), "3 days and 12 minutes"),
                  ((12 + 50e-6, False), "12 s and 50 µs"),
                  ((3 * 24 * 60 * 60 + 12 * 60, False), "3 d and 12 min"),
                  ((1e-6, False), "1 µs"),
                  ]
        for (i, eo) in values:
            o = units.readable_time(*i)
            self.assertEqual(o, eo,
                              u"%s is '%s' while expected '%s'" % (i, o, eo))

    def test_to_string_pretty(self):

        values = [
            0.000000041003,
            0.0051,
            0.014,
            0.39,
            0.230234543545,
        ]

        for sig in [2, 4, 6]:  # (None, 0, 1, 2, 4, 8):
            for v in values:
                self.assertEqual(
                    units.round_significant(v, sig),
                    float(units.to_string_pretty(v, sig, "s"))
                    )

                # print "sig: %s, val: %r, round: %s, pretty: %s" % (
                #                         sig,
                #                         v,
                #                         units.round_significant(v, sig),
                #                         units.to_string_pretty(v, sig, "s"))

    def test_decompose_si_prefix(self):

        values = [
            ("-12 nm", ("-12", "n", "m")),
            ("12 um", ("12", u"µ", "m")),
            ("12 um  ", ("12", u"µ", "m")),
            ("12 um  Hallo!", ("12 um  Hallo!", None, None)),
            ("3.2 Grap", ("3.2", "G", "rap")),
            ("-13.223    mm", ("-13.223", "m", "m")),
            ("-13.323 pm", ("-13.323", "p", "m")),
            ("-156.41e-9kN", ("-156.41e-9", "k", "N")),
            ("-156.41e-9 GN", ("-156.41e-9", "G", "N")),
            ("-156.41e-9    GN", ("-156.41e-9", "G", "N")),
            ("100.2", ("100.2", None, None)),
            ("100 m", ("100", None, "m")),
            ("banaan", ("banaan", None, None)),
            ("2 x 2 px", ("2 x 2 px", None, None)),
            ("2 x 2", ("2 x 2", None, None)),
            ("[3.0, 4.5]", ("[3.0, 4.5]", None, None)),
        ]

        for str_in, tuple_out in values:
            self.assertEqual(units.decompose_si_prefix(str_in), tuple_out)

    def test_decompose_si_prefix_explicit_unit(self):

        values = [
            (("-12 nm", "m"), ("-12", "n", "m")),
            (("-12 nm", "s"), ("-12 nm", None, None)),
            (("12 u", "s"), ("12", u"µ", None)),
            (("12 m", "s"), ("12", "m", None)),
            (("0.12 rad", "s"), ("0.12 rad", None, None)),
            (("0.12 rad", "rad"), ("0.12", None, "rad")),
        ]

        for args_in, exp_out in values:
            self.assertEqual(units.decompose_si_prefix(*args_in), exp_out)



if __name__ == "__main__":
    unittest.main()

    # suit = unittest.TestSuite()
    # # suit.addTest(PlotCanvasTestCase("test_plot_canvas"))
    # suit.addTest(TestUnits("test_to_string_pretty"))
    # runner = unittest.TextTestRunner()
    # runner.run(suit)


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
