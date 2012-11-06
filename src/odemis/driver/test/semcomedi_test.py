#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 6 Nov 2012

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.driver import semcomedi
import logging
import unittest

logging.getLogger().setLevel(logging.INFO)

# arguments used for the creation of basic components
CONFIG_SED = {"name": "sed", "role": "sed", "channel":5}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "channels": [0,1], "settle_time": 10e-6} 
CONFIG_SEM = {"name": "sem", "role": "sem", "device": "/dev/comedi0", 
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }

class TestSEM(unittest.TestCase):


    def test_creation(self):
        """
        Doesn't even try to acquire an image, just create and delete components
        """
        sem = semcomedi.SEMComedi(CONFIG_SEM)
        self.assertEqual(len(sem.children), 2)
        
        for child in sem.children:
            if child.name ==  CONFIG_SED["name"]:
                sed = child
            elif child.name ==  CONFIG_SCANNER["name"]:
                scanner = child 
        
        self.assertEqual(len(scanner.resolution.value), 2)
        
        self.assertTrue(sem.selfTest(), "SEM self test failed.")
        sem.terminate()
        
    def test_scan(self):
        devices = semcomedi.SEMComedi.scan()
        self.assertGreater(len(devices), 0)
        
        for name, kwargs in devices:
            print "opening ", name
            sem = semcomedi.SEMComedi("test", "sem", **kwargs)
            self.assertTrue(sem.selfTest(), "SEM self test failed.")
            
if __name__ == "__main__":
    unittest.main()