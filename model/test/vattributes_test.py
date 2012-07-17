#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 29 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
import model
import unittest
import weakref

class VigilantAttributeTest(unittest.TestCase):


    def setUp(self):
        pass


    def tearDown(self):
        pass


    def callback_test_notify(self, value):
        self.called += 1
        
    def test_notify_noinit(self):
        prop = model.IntVA(2)
        self.called = 0
        prop.subscribe(self.callback_test_notify)
        # now count
        prop.value = 3 # +1
        prop.value = 0 # +1
        prop.value = 0 # nothing because same value
        try:
            prop.value = 7.5
            self.fail("Assigning float to a int should not be allowed.")
        except model.InvalidTypeError:
            pass # as it should be
        prop.unsubscribe(self.callback_test_notify)
        
        self.assertTrue(prop.value == 0)
        self.assertTrue(self.called == 2)
    
    def test_notify_init(self):
        prop = model.FloatVA(2.0)
        self.called = 0
        # now count
        prop.subscribe(self.callback_test_notify, init=True) # +1
        prop.value = 3.0 # +1
        prop.value = 0 # +1
        prop.value = 0.0 # nothing because same value
        try:
            prop.value = "coucou"
            self.fail("Assigning string to a float should not be allowed.")
        except model.InvalidTypeError:
            pass # as it should be
        prop.unsubscribe(self.callback_test_notify)
        
        prop.value = 12 # no more counting
        
        self.assertTrue(prop.value == 12)
        self.assertTrue(self.called == 3)
    
    def test_readonly(self):
        prop = model.FloatVA(2.0, readonly=True)
        try:
            prop.value = 6.0
            self.fail("Modifying a readonly property should not be allowed.")
        except model.NotSettableError:
            pass # as it should be
        
        self.assertTrue(prop.value == 2)
        
    def test_list(self):
        prop = model.ListVA([2.0, 5, 4])
        self.called = 0
        # now count
        prop.subscribe(self.callback_test_notify, init=True) # +1
        prop.value = [3.0, 5] # +1
        prop.value = list((0,)) # +1
        prop.value = [0] # nothing because same value
        try:
            prop.value = 45
            self.fail("Assigning int to a list should not be allowed.")
        except model.InvalidTypeError:
            pass # as it should be
        prop.unsubscribe(self.callback_test_notify)
        
        prop.value = ["b"] # no more counting
        
        self.assertTrue(prop.value == ["b"])
        self.assertTrue(self.called == 3)
        
    def test_continuous(self):
        prop = model.FloatContinuous(2.0, [-1, 3.4])
        self.assertEqual(prop.value, 2)
        self.assertEqual(prop.range, (-1, 3.4))
        
        self.called = 0
        prop.subscribe(self.callback_test_notify)
        # now count
        prop.value = 3.0 # +1
        self.assertEqual(prop.value, 3)
        
        try:
            prop.value = 4.0
            self.fail("Assigning out of bound should not be allowed.")
        except model.OutOfBoundError:
            pass # as it should be
        
        try:
            prop.range = [-4.0, 1]
            self.fail("Assigning range not containing current value should not be allowed.")
        except model.OutOfBoundError:
            pass # as it should be
        
        try:
            prop.range = [12]
            self.fail("Range should be allowed only if it's a 2-tuple.")
        except model.InvalidTypeError:
            pass # as it should be
        
        prop.unsubscribe(self.callback_test_notify)
        
        self.assertTrue(self.called == 1)

    def test_enumerated(self):
        prop = model.StringEnumerated("a", set(["a", "c", "bfds"]))
        self.assertEqual(prop.value, "a")
        self.assertEqual(prop.choices, set(["a", "c", "bfds"]))
        
        self.called = 0
        prop.subscribe(self.callback_test_notify)
        # now count
        prop.value = "c" # +1
        assert(prop.value == "c")
        
        try:
            prop.value = "wfds"
            self.fail("Assigning out of bound should not be allowed.")
        except model.OutOfBoundError:
            pass # as it should be
        
        prop.choices = set(["a", "c", "b", 5])
        assert(prop.value == "c")
        try:
            prop.choices = set(["a", "b"])
            self.fail("Assigning choices not containing current value should not be allowed.")
        except model.OutOfBoundError:
            pass # as it should be
        
        try:
            prop.value = 5
            self.fail("Assigning an int to a string should not be allowed.")
        except model.InvalidTypeError:
            pass # as it should be
        
        try:
            prop.choices = 5
            self.fail("Choices should be allowed only if it's a set.")
        except model.InvalidTypeError:
            pass # as it should be
        
        prop.unsubscribe(self.callback_test_notify)
        
        self.assertTrue(self.called == 1)

    def test_weakref(self):
        """
        checks that even if an object has a method subscribed to a property,
          it will be garbage-collected when not used anymore and its
          subscription dropped.
        """
        prop = model.FloatVA(2.0)
        o = LittleObject()
        wo = weakref.ref(o)
        assert(wo() is not None)
        
        prop.subscribe(o.callback)
        prop.value = 6.0 # +1
        assert(o.called == 1)
        
        del o
        assert(wo() is None)
        
        prop.value = 1
        assert(prop.value == 1)
           
class LittleObject(object):
    def __init__(self):
        self.called = 0
        
    def callback(self, value):
        self.called += 1
        
if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()