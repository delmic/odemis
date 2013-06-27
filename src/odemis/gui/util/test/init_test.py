# -*- coding: utf-8 -*-
'''
Created on 24 Jan 2013

@author: piel

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
# test the functions of the gui.util.__init__ module
from odemis.gui.util import limit_invocation, formats_to_wildcards
import logging
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

class TestLimitInvocation(unittest.TestCase):
    def test_not_too_often(self):
        self.count = 0
        now = time.time()
        end = now + 1.1 # a bit more than 1 s
        while time.time() < end:
            self.count_max_1s()
            time.sleep(0.01)

        self.assertLessEqual(self.count, 2, "method was called more than twice in 1 second: %d" % self.count)
        
        time.sleep(2) # wait for the last potential calls to happen
        self.assertLessEqual(self.count, 3, "method was called more than three times in 2 seconds: %d" % self.count)        
        
    @limit_invocation(1)
    def count_max_1s(self):
        # never called more than once per second
        self.count += 1

class TestFormat(unittest.TestCase):
    def test_formats_to_wildcards(self):
        inp = {"HDF5":[".h5", ".hdf5"]}
        exp_out = ("HDF5 files (*.h5;*.hdf5)|*.h5;*.hdf5",
                   ["HDF5"])
        out = formats_to_wildcards(inp)
        self.assertEqual(out, exp_out)



if __name__ == "__main__":
    unittest.main()
