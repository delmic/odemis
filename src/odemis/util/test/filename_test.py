# -*- coding: utf-8 -*-
"""
Created on 29 Jan 2018

@author: Philip Winkler

Copyright © 2018 Philip Winkler, Delmic

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

from odemis.gui.util import get_home_folder
from odemis.util.filename import create_filename, guess_pattern, update_counter, make_unique_name
import os
import time
import unittest

date = time.strftime("%Y%m%d")
daterev = time.strftime("%d%m%Y")
timeshrt = time.strftime("%H%M")
timeshrt_colon = time.strftime("%H:%M")
timelng = time.strftime("%H%M%S")
timelng_hyphen = time.strftime("%H-%M-%S")
timelng_colon = time.strftime("%H:%M:%S")
# date_sl = time.strftime("%Y/%m/%d")
# timelng_sl = time.strftime("%H/%M/%S")
current_year = time.strftime("%Y")
dshrtrev = time.strftime("%d%m%y")
# Note: dateshrt cannot be used in tests, as on days similar to the year, such as 20-10-20,
# it's not clear what is the day and what is the year, but the code convention is to
# guess DDMMYY (aka dshrtrev).
# dateshrt = time.strftime("%y%m%d")
dshrtrev_hyphen = time.strftime("%d-%m-%y")

EXTS = ('.tiff', '.ome.tiff', '.0.ome.tiff', '.h5', '.hdf5')
PATH = get_home_folder()


class TestFilenameSuggestions(unittest.TestCase):
    """
    Tests the util-acquisition functions for filename suggestions. 
    """

    def test_guess_pattern(self):
        fn_ptns = {
                   'test-123': ('test-{cnt}', '123'),
                   '%stest-123' % date: ('{datelng}test-{cnt}', '123'),
                   '123-test-%s' % date: ('{cnt}-test-{datelng}', '123'),
                   # This doesn't work "23-%s" % timelng_hyphen, but it's fine, as it's too much a corner case to be sure it was time
                   'test-123 %s--' % timelng_hyphen: ('test-{cnt} {timelng_hyphen}--', '123'),
                   'test-123-%s' % timeshrt_colon: ('test-{cnt}-{timeshrt_colon}', '123'),
                   'test-%s-123-' % timelng_colon: ('test-{timelng_colon}-{cnt}-', '123'),
                   '%s%s-acquisition' % (date, timelng): ('{datelng}{timelng}-acquisition', '001'),
                   'test-0070': ('test-{cnt}', '0070'),
                   '4580-test-%s' % dshrtrev: ('{cnt}-test-{dshrtrev}', '4580'),
                   '4580-test:%s' % dshrtrev_hyphen: ('{cnt}-test:{dshrtrev_hyphen}', '4580'),
                   '%s%s' % (daterev, timelng): ('{daterev}{timelng}', '001'),
                   'test2-45': ('test2-{cnt}', '45'),
                   'test 1980-08-23': ('test 1980-08-{cnt}', '23'),  # a date, but not *now*
                   'test': ('test-{cnt}', '001'),
                   '%s-cell5' % current_year: ('{year}-cell{cnt}', '5'),
                   '%s-{cell}{cnt}' % current_year: ('{year}-{{cell}}{{cnt}}', '001')
                    }

        for fn, ptn in fn_ptns.items():
            for ext in EXTS:
                self.assertEqual(guess_pattern(fn), ptn)
                fullfn = os.path.join(PATH, fn) + ext
                self.assertEqual(guess_pattern(fullfn), ptn)

    def test_create_filename(self):
        # Test some time related patterns later, so that the right time is used
        fn_ptns = {
                   'test-123': ('test-{cnt}', '123'),
                   '%stest-123' % date: ('{datelng}test-{cnt}', '123'),
                   '123-test-%s' % date: ('{cnt}-test-{datelng}', '123'),
                   'test-0000': ('test-{cnt}', '0000'),
                   'test2-45': ('test2-{cnt}', '45'),
                   '%s-cell5' % current_year: ('{year}-cell{cnt}', '5'),
                    }

        for fn, ptn in fn_ptns.items():
            for ext in EXTS:
                fullfn = os.path.join(PATH, fn) + ext
                self.assertEqual(create_filename(PATH, ptn[0], ext, ptn[1]), fullfn)

        # Assertion takes ~ 1e-4 seconds, so it's safe to assume that the time hasn't changed
        self.assertEqual(create_filename(PATH, 'test-{cnt}-{timeshrt_hyphen}', '.0.ome.tiff', '123'),
                         os.path.join(PATH, 'test-123-%s.0.ome.tiff' % time.strftime('%H-%M')))
        self.assertEqual(create_filename(PATH, 'test-{cnt}-{timeshrt_colon}', '.0.ome.tiff', '123'),
                         os.path.join(PATH, 'test-123-%s.0.ome.tiff' % time.strftime('%H:%M')))
        self.assertEqual(create_filename(PATH, '{datelng}{timelng}-acquisition', '.tiff', '001'),
                         os.path.join(PATH, '%s-acquisition.tiff' % time.strftime('%Y%m%d%H%M%S')))
        self.assertEqual(create_filename(PATH, '{daterev}{timelng}', '.tiff', '001'),
                         os.path.join(PATH, '%s.tiff' % time.strftime('%d%m%Y%H%M%S')))

    def test_filename_is_unique(self):

        fns = {
            'test-123': 'test-124',
            'test 0800 great': 'test 0801 great',
            'test-%s-1' % time.strftime('%Y%m%d'): 'test-%s-2' % time.strftime('%Y%m%d'),
            'booo': 'booo-001',
            }

        for fn, new_fn in fns.items():
            ext = '.0.ome.tiff'
            # Create file
            open('./%s%s' % (fn, ext), "w+").close()
            ptn, cnt = guess_pattern(fn)
            new_fullfn = os.path.join('.', new_fn) + ext
            next_fullfn = create_filename('.', ptn, ext, cnt)
            self.assertEqual(next_fullfn, new_fullfn)
            os.remove('./%s%s' % (fn, ext))

        # Check what happens is next proposed file is also already in directory
        open('./test-123.tiff', "w+").close()
        open('./test-124.tiff', "w+").close()

        ptn, cnt = guess_pattern('./test-123')
        new_fullfn = os.path.join('.', 'test-125.tiff')
        self.assertEqual(create_filename('.', ptn, '.tiff', cnt), new_fullfn)

        os.remove('./test-123.tiff')
        os.remove('./test-124.tiff')

    def test_update_counter(self):
        self.assertEqual(update_counter('0'), '1')
        self.assertEqual(update_counter('0005'), '0006')
        self.assertEqual(update_counter('9'), '10')
        self.assertEqual(update_counter('000'), '001')
        self.assertRaises(AssertionError, update_counter, '-5')

    def test_make_unique_name(self):
        self.assertEqual(make_unique_name('abc', []), 'abc')
        self.assertEqual(make_unique_name('abc', ['abc']), 'abc-1')
        self.assertEqual(make_unique_name('abc', ['abc', 'abc-1']), 'abc-2')
        self.assertEqual(make_unique_name('abc-1', ['abc', 'abc-1']), 'abc-2')
        self.assertEqual(make_unique_name('abc-2', ['abc', 'abc-2']), 'abc-3')
        self.assertEqual(make_unique_name('abc', ['abc', 'abc-2']), 'abc-1')
        self.assertEqual(make_unique_name('abc', ['abc', 'abc-1', 'abc-2']), 'abc-3')
        self.assertEqual(make_unique_name('abc-1', ['abc', 'abc-1', 'abc-2']), 'abc-3')
        self.assertEqual(make_unique_name('abc-0', ['abc-1']), 'abc-0')
        self.assertEqual(make_unique_name('abc-0', ['abc-0']), 'abc-1')
        self.assertEqual(make_unique_name('abc-1abc', ['abc-1abc']), 'abc-2abc')
        self.assertEqual(make_unique_name('abc-1s-0.5d-1', ['abc-1s-0.5d-1']), 'abc-1s-0.5d-2')


if __name__ == "__main__":
    unittest.main()
