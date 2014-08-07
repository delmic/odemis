# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import collections
import logging
from odemis.util import fluo
import unittest


logging.getLogger().setLevel(logging.DEBUG)

class FluoTestCase(unittest.TestCase):
    
    def test_estimate(self):
        # inputs, expected
        in_exp = [((500e-9, (490e-9, 510e-9)), fluo.FIT_GOOD), # 2-float band
                  ((500e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.FIT_GOOD), # 5-float band
                  ((489e-9, (490e-9, 510e-9)), fluo.FIT_BAD), # almost good
                  ((489e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.FIT_BAD), # almost good
                  ((515e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.FIT_BAD), # almost good
                  ((900e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.FIT_IMPOSSIBLE), # really bad
                  ]
        for args, exp in in_exp:
            out = fluo.estimate_fit_to_dye(*args)
            self.assertEqual(exp, out, "Failed while running with %s and got %s" % (args, out))

    def test_quantify(self):
        """
        compare quantify and fit
        """
        # inputs, expected
        ins = [(500e-9, (490e-9, 510e-9)),
               (500e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)),
               (489e-9, (490e-9, 510e-9)),
               (489e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)),
               (515e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)),
               (900e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)),
               ]
        for args in ins:
            est = fluo.estimate_fit_to_dye(*args)
            quant = fluo.quantify_fit_to_dye(*args)
            if quant == 0:
                self.assertEqual(est, fluo.FIT_IMPOSSIBLE)
            else:
                self.assertIn(est, {fluo.FIT_BAD, fluo.FIT_GOOD})

    def test_find_best_easy(self):
        # tests with 2-float bands
        bands = {(490e-9, 510e-9),
                 (400e-9, 413e-9),
                 ((650e-9, 680e-9), (780e-9, 812e-9), (1034e-9, 1500e-9))
                }
        # try with "easy" values: the center
        for b in bands:
            # pick a good wl, and check the function finds it
            if isinstance(b[0], collections.Iterable):
                sb = b[-1]
            else:
                sb = b
            wl = sum(sb) / len(sb)
            out = fluo.find_best_band_for_dye(wl, bands)
            self.assertEqual(b, out, "find_best(%f, %s) returned %s while expected %s" % (wl, bands, out, b))

        # tests with 5-float bands
        bands = {(490e-9, 497e-9, 500e-9, 503e-9, 510e-9),
                 (400e-9, 405e-9, 407e-9, 409e-9, 413e-9),
                 ((650e-9, 660e-9, 675e-9, 678e-9, 680e-9),
                  (780e-9, 785e-9, 790e-9, 800e-9, 812e-9),
                  (1034e-9, 1080e-9, 1100e-9, 1200e-9, 1500e-9)
                 )
                }
        # try with "easy" values: the center
        for b in bands:
            # pick a good wl, and check the function finds it
            if isinstance(b[0], collections.Iterable):
                sb = b[0]
            else:
                sb = b
            wl = sum(sb) / len(sb)
            out = fluo.find_best_band_for_dye(wl, bands)
            self.assertEqual(b, out, "find_best(%f, %s) returned %s while expected %s" % (wl, bands, out, b))

    def test_find_best_hard(self):
        # tests with 2-bands
        bands = {(490e-9, 510e-9),
                 (400e-9, 413e-9),
                 ((650e-9, 680e-9), (780e-6, 812e-9), (1034e-9, 1500e-9))
                }
        # try with "hard" values: the border
        for b in bands:
            # pick a good wl, and check the function finds it
            if isinstance(b[0], collections.Iterable):
                sb = b[-1]
            else:
                sb = b
            wl = sb[1]
            out = fluo.find_best_band_for_dye(wl, bands)
            self.assertEqual(b, out, "find_best(%f, %s) returned %s while expected %s" % (wl, bands, out, b))

        # tests with 5-float bands
        bands = {(490e-9, 497e-9, 500e-9, 503e-9, 510e-9),
                 (400e-9, 405e-9, 407e-9, 409e-9, 413e-9),
                 ((650e-9, 660e-9, 675e-9, 678e-9, 680e-9),
                  (780e-9, 785e-9, 790e-9, 800e-9, 812e-9),
                  (1034e-9, 1080e-9, 1100e-9, 1200e-9, 1500e-9)
                 )
                }
        # try with "hard" values: the border
        for b in bands:
            # pick a good wl, and check the function finds it
            if isinstance(b[0], collections.Iterable):
                sb = b[1]
            else:
                sb = b
            wl = sb[0]
            out = fluo.find_best_band_for_dye(wl, bands)
            self.assertEqual(b, out, "find_best(%f, %s) returned %s while expected %s" % (wl, bands, out, b))

    def test_find_best_overlap(self):
        # tests with overlapping 2-bands
        bands = {(490e-9, 510e-9), # 500/10 nm
                 (400e-9, 413e-9),
                 (4000e-9, 600e-9), # 500/100 nm
                 ((650e-9, 680e-9), (780e-9, 812e-9), (1034e-9, 1500e-9))
                }
        wl = 500e-9
        out = fluo.find_best_band_for_dye(wl, bands)
        b = (490e-9, 510e-9)
        self.assertEqual(b, out, "find_best(%f, %s) returned %s while expected %s" % (wl, bands, out, b))


