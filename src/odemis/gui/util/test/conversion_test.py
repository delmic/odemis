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
import unittest
from odemis.gui.util import conversion

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



if __name__ == "__main__":
    unittest.main()


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
