# -*- coding: utf-8 -*-
'''
Created on 7 Dec 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS F

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.driver import spectrapro
import logging
import os
import unittest

logging.getLogger().setLevel(logging.DEBUG)

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttySP" #"/dev/ttyUSB0"

CLASS = spectrapro.FakeSpectraPro # use FakeSpectraPro if not hardware present
KWARGS = {"name": "test", "role": "spectrograph", "port": PORT}

#@unittest.skip("faster") 
class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    def test_scan(self):
        # TODO skip if Fake
        devices = CLASS.scan()
        self.assertGreater(len(devices), 0)
        
        for name, kwargs in devices:
            print "opening ", name
            sem = CLASS(name, "spec", **kwargs)
            self.assertTrue(sem.selfTest(), "self test failed.")
        
    def test_creation(self):
        """
        Doesn't even try to acquire an image, just create and delete components
        """
        sp = CLASS(**KWARGS)
        
        self.assertGreater(len(sp.grating.choices), 0)
        
        self.assertTrue(sp.selfTest(), "self test failed.")
        sp.terminate()
    