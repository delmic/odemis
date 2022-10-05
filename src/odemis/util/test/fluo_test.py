# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2014

@author: Éric Piel

Copyright © 2014-2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from collections.abc import Iterable
import logging
from odemis.model import BAND_PASS_THROUGH
from odemis.util import fluo
import unittest


logging.getLogger().setLevel(logging.DEBUG)

class FluoTestCase(unittest.TestCase):

    def test_center(self):
        in_exp = [((490e-9, 510e-9), 500e-9), # 2-float band
                  ((490e-9, 497e-9, 500e-9, 503e-9, 510e-9), 500e-9), # 5-float band
                  (((490e-9, 510e-9), (820e-9, 900e-9)), (500e-9, 860e-9)) # multi-band
                  ]
        for inp, exp in in_exp:
            out = fluo.get_center(inp)
            self.assertEqual(exp, out, "Failed while running with %s and got %s" % (inp, out))

        # Special case for "pass-through": any number > 0 is fine
        out = fluo.get_center(BAND_PASS_THROUGH)
        self.assertGreaterEqual(out, 0)

    def test_one_band_em(self):
        em_band = (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)
        em_bands = ((650e-9, 660e-9, 675e-9, 678e-9, 680e-9),
                    (780e-9, 785e-9, 790e-9, 800e-9, 812e-9),
                    (1034e-9, 1080e-9, 1100e-9, 1200e-9, 1500e-9))
        # Excitation band should be smaller than the emission band used
        in_exp = [((em_band, (490e-9, 510e-9)), em_band), # only one band
                  ((em_bands, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), em_bands[0]), # smallest above 500nm
                  ((em_bands, (690e-9, 697e-9, 700e-9, 703e-9, 710e-9)), em_bands[1]), # smallest above 700nm
                  ((em_bands, (790e-9, 797e-9, 800e-9, 803e-9, 810e-9)), em_bands[2]), # smallest above 800nm
                  ((em_bands[0:2], (790e-9, 797e-9, 800e-9, 803e-9, 810e-9)), em_bands[1]), # biggest
                  ((em_bands, (790e-9, 797e-9, 800e-9, 803e-9, 810e-9)), em_bands[2]),  # smallest above 800nm
                  ((em_bands, ((490e-9, 510e-9), (550e-9, 570e-9))), em_bands[0]),
                  # It should also work with lists (although not very officially supported
                  (([[400e-9, 500e-9], [500e-9, 600e-9]], [490e-9, 510e-9]), (500e-9, 600e-9)),  # smallest above 500nm
                  # Try with a pass-through
                  ((em_band, BAND_PASS_THROUGH), em_band),
                  ((em_bands, BAND_PASS_THROUGH), em_bands[-1]),  # biggest
                  ((BAND_PASS_THROUGH, (490e-9, 510e-9)), BAND_PASS_THROUGH),
                  ]
        for args, exp in in_exp:
            out = fluo.get_one_band_em(*args)
            self.assertEqual(exp, out, "Failed while running with %s and got %s" % (args, out))

    def test_one_center_em(self):
        em_band = (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)
        em_bands = ((650e-9, 660e-9, 675e-9, 678e-9, 680e-9),
                    (780e-9, 785e-9, 790e-9, 800e-9, 812e-9),
                    (1034e-9, 1080e-9, 1100e-9, 1200e-9, 1500e-9))
        # Excitation band should be smaller than the emission band used
        in_exp = [((em_band, (490e-9, 510e-9)), fluo.get_center(em_band)), # only one band
                  ((em_bands, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.get_center(em_bands[0])), # smallest above 500nm
                  ((em_bands, (690e-9, 697e-9, 700e-9, 703e-9, 710e-9)), fluo.get_center(em_bands[1])), # smallest above 700nm
                  ((em_bands, (790e-9, 797e-9, 800e-9, 803e-9, 810e-9)), fluo.get_center(em_bands[2])), # smallest above 800nm
                  ((em_bands[0:2], (790e-9, 797e-9, 800e-9, 803e-9, 810e-9)), fluo.get_center(em_bands[1])), # biggest
                  # Try with a pass-through
                  ((em_band, BAND_PASS_THROUGH), 500e-9),
                  ((em_bands, BAND_PASS_THROUGH), 1100e-9),  # biggest
                  # ((BAND_PASS_THROUGH, (490e-9, 510e-9)), BAND_PASS_THROUGH),
                  ]
        for args, exp in in_exp:
            out = fluo.get_one_center_em(*args)
            self.assertEqual(exp, out, "Failed while running with %s and got %s" % (args, out))

    def test_one_band_ex(self):
        ex_band = (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)
        ex_bands = ((650e-9, 660e-9, 675e-9, 678e-9, 680e-9),
                    (780e-9, 785e-9, 790e-9, 800e-9, 812e-9),
                    (1034e-9, 1080e-9, 1100e-9, 1200e-9, 1500e-9))
        # Excitation band should be smaller than the emission band used
        in_exp = [((ex_band, (490e-9, 510e-9)), ex_band),  # only one band
                  ((ex_bands, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), ex_bands[0]),  # nothing fitting, but should pick the smallest
                  ((ex_bands, (690e-9, 697e-9, 700e-9, 703e-9, 710e-9)), ex_bands[0]),  # biggest below 700nm
                  ((ex_bands, (790e-9, 797e-9, 800e-9, 803e-9, 810e-9)), ex_bands[1]),  # biggest below 800nm
                  # Try with a pass-through
                  ((ex_band, BAND_PASS_THROUGH), ex_band),
                  ((ex_bands, BAND_PASS_THROUGH), ex_bands[0]),  # smallest
                  ((BAND_PASS_THROUGH, (490e-9, 510e-9)), BAND_PASS_THROUGH),
                  ]
        for args, exp in in_exp:
            out = fluo.get_one_band_ex(*args)
            self.assertEqual(exp, out, "Failed while running with %s and got %s" % (args, out))

    def test_one_center_ex(self):
        ex_band = (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)
        ex_bands = ((650e-9, 660e-9, 675e-9, 678e-9, 680e-9),
                    (780e-9, 785e-9, 790e-9, 800e-9, 812e-9),
                    (1034e-9, 1080e-9, 1100e-9, 1200e-9, 1500e-9))
        # Excitation band should be smaller than the emission band used
        in_exp = [((ex_band, (490e-9, 510e-9)), fluo.get_center(ex_band)), # only one band
                  ((ex_bands, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.get_center(ex_bands[0])), # nothing fitting, but should pick the smallest
                  ((ex_bands, (690e-9, 697e-9, 700e-9, 703e-9, 710e-9)), fluo.get_center(ex_bands[0])), # biggest below 700nm
                  ((ex_bands, (790e-9, 797e-9, 800e-9, 803e-9, 810e-9)), fluo.get_center(ex_bands[1])), # biggest below 800nm
                  ]
        for args, exp in in_exp:
            out = fluo.get_one_center_ex(*args)
            self.assertEqual(exp, out, "Failed while running with %s and got %s" % (args, out))

    def test_estimate(self):
        # inputs, expected
        in_exp = [((500e-9, (490e-9, 510e-9)), fluo.FIT_GOOD), # 2-float band
                  ((500e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.FIT_GOOD), # 5-float band
                  ((489e-9, (490e-9, 510e-9)), fluo.FIT_BAD), # almost good
                  ((489e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.FIT_BAD), # almost good
                  ((515e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.FIT_BAD), # almost good
                  ((900e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)), fluo.FIT_IMPOSSIBLE), # really bad
                  ((500e-9, BAND_PASS_THROUGH), fluo.FIT_IMPOSSIBLE),  # really bad
                  ]
        for args, exp in in_exp:
            out = fluo.estimate_fit_to_dye(*args)
            self.assertEqual(exp, out, "Failed while running with %s and got %s" % (args, out))

    def test_quantify(self):
        """
        compare quantify and fit
        """
        # inputs
        ins = [(500e-9, (490e-9, 510e-9)),
               (500e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)),
               (489e-9, (490e-9, 510e-9)),
               (489e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)),
               (515e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)),
               (900e-9, (490e-9, 497e-9, 500e-9, 503e-9, 510e-9)),
               (500e-9, BAND_PASS_THROUGH)
               ]
        for args in ins:
            est = fluo.estimate_fit_to_dye(*args)
            quant = fluo.quantify_fit_to_dye(*args)
            if quant < 1e4:  # Just rough estimate
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
            if isinstance(b[0], Iterable):
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
            if isinstance(b[0], Iterable):
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
            if isinstance(b[0], Iterable):
                sb = b[-1]
            else:
                sb = b
            wl = sb[1]
            out = fluo.find_best_band_for_dye(wl, bands)
            self.assertEqual(b, out, "find_best(%f, %s) returned %s while expected %s" % (wl, bands, out, b))

        # tests with 5-float bands
        bands = ((490e-9, 497e-9, 500e-9, 503e-9, 510e-9),
                 (400e-9, 405e-9, 407e-9, 409e-9, 413e-9),
                 ((650e-9, 660e-9, 675e-9, 678e-9, 680e-9),
                  (780e-9, 785e-9, 790e-9, 800e-9, 812e-9),
                  (1034e-9, 1080e-9, 1100e-9, 1200e-9, 1500e-9)
                 )
                )
        # try with "hard" values: the border
        for b in bands:
            # pick a good wl, and check the function finds it
            if isinstance(b[0], Iterable):
                sb = b[1]
            else:
                sb = b
            wl = sb[0]
            out = fluo.find_best_band_for_dye(wl, bands)
            self.assertEqual(b, out, "find_best(%f, %s) returned %s while expected %s" % (wl, bands, out, b))

        # Try completely out: at least it should pick the closest from the wl
        for i, wl in ((0, 540e-9), (1, 360e-9)):
            exb = bands[i]
            out = fluo.find_best_band_for_dye(wl, bands)
            self.assertEqual(exb, out, "find_best(%f, %s) returned %s while expected %s" % (wl, bands, out, exb))

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

    def test_to_readable_band(self):
        # inputs, expected
        in_exp = [((490e-9, 510e-9), "500/20 nm"),  # 2-float band
                  (((490e-9, 510e-9), (590e-9, 610e-9)), "500, 600 nm"), # multi-band
                  (BAND_PASS_THROUGH, u"pass-through"),  # just a string
                  ]
        for arg, exp in in_exp:
            out = fluo.to_readable_band(arg)
            self.assertEqual(exp, out, "Failed while running with %s and got %s" % (arg, out))


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
