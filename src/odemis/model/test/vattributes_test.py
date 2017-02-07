#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 29 Mar 2012

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
from __future__ import division

import logging
from odemis import model
import pickle
import unittest
from unittest.case import skip
import weakref
import numpy

logging.getLogger().setLevel(logging.DEBUG)

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
        except TypeError:
            pass # as it should be
        prop.unsubscribe(self.callback_test_notify)

        self.assertTrue(prop.value == 0)
        self.assertTrue(self.called == 2)

    def test_pretty_str(self):
        prop = model.IntVA(2)
        pretty_str = str(prop)
        self.assertIn("IntVA", pretty_str)
        self.assertIn(str(prop.value), pretty_str)

        prop = model.ListVA([2, 3], unit=u"µm")
        pretty_str = unicode(prop)
        self.assertIn("ListVA", pretty_str)
        self.assertIn(str(prop.value), pretty_str)

        prop = model.FloatContinuous(2.3, unit=u"µm", range=(1.0, 9))
        pretty_str = unicode(prop)
        self.assertIn("FloatContinuous", pretty_str)
        self.assertIn(unicode(prop.value), pretty_str)

    def test_unsubscribe(self):
        prop = model.IntVA(2)
        self.called = 0
        prop.subscribe(self.callback_test_notify)
        # now count
        prop.value = 3 # +1
        prop.unsubscribe(self.callback_test_notify)
        prop.value = 0 # +0
        prop.value = 0 # +0
        prop.value = 1 # +0
        prop.value = 0 # +0

        self.assertTrue(prop.value == 0)
        self.assertTrue(self.called == 1)

        # Bounded and unbounded methods are treated differently, so test both
        def unbound_func(v):
            self.called += 1

        self.called = 0
        prop.subscribe(unbound_func)
        # now count
        prop.value = 3 # +1
        prop.unsubscribe(unbound_func)
        prop.value = 0 # +0
        prop.value = 0 # +0
        prop.value = 1 # +0
        prop.value = 0 # +0

        self.assertTrue(prop.value == 0)
        self.assertTrue(self.called == 1)

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
        except TypeError:
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

        # List assignment
        prop.value = [3.0, 5] # +1
        prop.value = list((0,)) # +1
        prop.value = [0] # nothing because same value

        # Item removal
        del prop.value[0] # +1
        self.assertEqual(prop.value, [])

        prop.value = [1, 2, 3, 4] # +1

        del prop.value[1:-1] # +1
        self.assertEqual(prop.value, [1, 4])

        # Insert and remove item
        prop.value.insert(1, 66) # +1
        self.assertEqual(prop.value, [1, 66, 4])
        prop.value.remove(66) # +1
        self.assertEqual(prop.value, [1, 4])

        # Item adding
        prop.value += [44] # +1
        self.assertEqual(prop.value, [1, 4, 44])
        self.assertEqual(self.called, 9, "Called has value %s" % self.called)

        prop.value.extend([43, 42]) # +1
        prop.value.extend([]) # The list value stays the same, so no increase!!
        self.assertEqual(prop.value, [1, 4, 44, 43, 42])

        prop.value.append(41) # +1
        self.assertEqual(prop.value, [1, 4, 44, 43, 42, 41])

        # In place repetition
        orig_len = len(prop.value)
        prop.value *= 3 # +1
        self.assertEqual(len(prop.value), orig_len * 3)
        self.assertEqual(self.called, 12, "Called has value %s" % self.called)

        # Item assignment
        prop.value = range(5) # +1
        prop.value[1] = 5 # +1
        prop.value[1] = 5 # +0
        self.assertEqual(prop.value, [0, 5, 2, 3, 4])
        self.assertEqual(prop.value.pop(), 4) # +1

        # Item sorting
        prop.value.sort() # +1
        self.assertEqual(prop.value, [0, 2, 3, 5])
        prop.value.reverse() # +1
        self.assertEqual(prop.value, [5, 3, 2, 0])

        # pl = pickle.dumps(prop.value, pickle.HIGHEST_PROTOCOL)
        # unpl = pickle.loads(pl)
        # self.assertEqual(unpl, prop.value)

        try:
            prop.value = 45
            self.fail("Assigning int to a list should not be allowed.")
        except TypeError:
            pass # as it should be
        prop.unsubscribe(self.callback_test_notify)
        self.assertEqual(self.called, 17, "Called has value %s" % self.called)

        prop.value = ["b"] # no more counting

        self.assertEqual(prop.value, ["b"])
        self.assertEqual(self.called, 17, "Called has value %s" % self.called)

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
        except IndexError:
            pass # as it should be

        try:
            prop.range = [-4.0, 1.0]
            self.fail("Assigning range not containing current value should not be allowed.")
        except IndexError:
            pass # as it should be

        try:
            prop.clip_on_range = True
            self.assertEqual(prop.value, 3.0, "Value should not have changed yet")
            prop.range = [-4.0, 1.0]
            self.assertEqual(prop.value, 1.0, "Value should have been clipped")
            prop.clip_on_range = False
        except IndexError:
            pass # as it should be

        try:
            prop.range = [12]
            self.fail("Range should be allowed only if it's a 2-tuple.")
        except TypeError:
            pass # as it should be

        prop.unsubscribe(self.callback_test_notify)

        self.assertTrue(self.called == 2)

        # Test a bit the IntContinuous
        prop2 = model.IntContinuous(2, [1, 34], unit="px")
        self.assertEqual(prop2.value, 2)
        self.assertEqual(prop2.range, (1, 34))

        prop2.value = 30
        self.assertEqual(prop2.value, 30)
        self.assertIsInstance(prop2.value, int)

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
        except IndexError:
            pass # as it should be

        prop.choices = set(["a", "c", "b", 5])
        assert(prop.value == "c")
        try:
            prop.choices = set(["a", "b"])
            self.fail("Assigning choices not containing current value should not be allowed.")
        except IndexError:
            pass # as it should be

        try:
            prop.value = 5
            self.fail("Assigning an int to a string should not be allowed.")
        except TypeError:
            pass # as it should be

        try:
            prop.choices = 5
            self.fail("Choices should be allowed only if it's a set.")
        except TypeError:
            pass # as it should be

        prop.unsubscribe(self.callback_test_notify)

        self.assertTrue(self.called == 1)

        # It's also allowed to use dict as choices
        prop = model.VAEnumerated((1, 2), {(1, 2): "aaa", (3, 5): "doo"})
        for v in prop.choices:
            prop.value = v # they all should work


    def test_resolution(self):
        va = model.ResolutionVA((10,10), ((1,1), (100, 150)))
        self.assertEqual(va.value, (10,10))
        self.assertEqual(va.range, ((1,1), (100, 150)))

        # must convert anything to a tuple
        va.value = [11, 150]
        self.assertEqual(va.value, (11, 150))

        # must not accept resolutions with float
        try:
            va.value = (8., 160)
            self.fail("Assigning non int values should not be allowed.")
        except TypeError:
            pass # as it should be

        # must not accept resolutions outside of the range
        try:
            va.value = (80, 160)
            self.fail("Assigning value not in range should not be allowed.")
        except IndexError:
            pass # as it should be

        try:
            va.value = (10,10,10)
            self.fail("Assigning a 3-tuple to a resolution should not be allowed.")
        except TypeError:
            pass # as it should be

    def test_tc(self):
        """
        TupleContinuous
        """
        va = model.TupleContinuous((0.1,10,.5), ((-1.3,12,0), (100.,150.,1.)), cls=(int, long, float))
        self.assertEqual(va.value, (0.1,10,.5))
        self.assertEqual(va.range, ((-1.3,12,0), (100.,150.,1.)))

        # must convert anything to a tuple
        va.value = [-1, 150, .5]
        self.assertEqual(va.value, (-1, 150, .5))

        # must not accept values outside of the range
        try:
            va.value = (-1., 160., .5)
            self.fail("Assigning value not in range should not be allowed.")
        except IndexError:
            pass # as it should be

        try:
            va.value = (10.,10.)
            self.fail("Assigning a 2-tuple to a 3-tuple should not be allowed.")
        except TypeError:
            pass # as it should be

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

    def delegate_set_float(self, value):
        return value + 1.0 # unusual behaviour

    def test_setter(self):
        """
        check the delegation
        """
        prop = model.FloatVA(2.0, setter=self.delegate_set_float)
        # maybe it should be 3.0? But it's better not to call the delegate setter
        # anyway, the owner can always also call the setter by itself
        self.assertEqual(prop.value, 2.0)
        prop.value = 10.0
        self.assertEqual(prop.value, 11.0)

    def test_tuple_of_tuple_of_numpyarray(self):
        prop = model.VigilantAttribute(None)

        self.called = 0
        # now count
        prop.subscribe(self.callback_test_notify)
        first_value = ((numpy.zeros(42),),)
        # first value
        prop.value = first_value # +1
        # the same object as first value
        prop.value = first_value
        # test passing the same values, but with a different object
        prop.value = ((numpy.zeros(42),),) # +1
        self.assertEqual(self.called, 2, "Called has value %s" % self.called)
        # empty tuple
        prop.value = () # +1
        # empty tuple inside e tuple
        prop.value = ((),) # +1
        self.assertEqual(self.called, 4, "Called has value %s" % self.called)

class LittleObject(object):
    def __init__(self):
        self.called = 0

    def callback(self, value):
        self.called += 1

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
