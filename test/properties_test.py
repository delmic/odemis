'''
Created on 29 Mar 2012

@author: piel
'''
import unittest
import model

class PropertiesTest(unittest.TestCase):


    def setUp(self):
        pass


    def tearDown(self):
        pass


    def callback_test_notify(self, value):
        self.called += 1
        
    def test_notify_noinit(self):
        prop = model.IntProperty(2)
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
        
        assert(prop.value == 0)
        assert(self.called == 2)
    
    def test_notify_init(self):
        prop = model.FloatProperty(2.0)
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
        
        prop.value = 0.0 # no more counting
        
        assert(prop.value == 0)
        assert(self.called == 3)

        
    def test_continuous(self):
        prop = FloatContinuous(2.0, [-1, 3.4])
        assert(prop.value == 2)
        assert(prop.range == (-1, 3.4))
        
        self.called = 0
        prop.subscribe(self.callback_test_notify)
        # now count
        prop.value = 3.0 # +1
        assert(prop.value == 3)
        
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
        
        assert(self.called == 1)

    def test_enumerated(self):
        prop = StringEnumerated("a", set(["a", "c", "bfds"]))
        assert(prop.value == "a")
        assert(prop.choices == set(["a", "c", "bfds"]))
        
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
        
        prop.choices = set(["a", "c", "b"])
        assert(prop.value == "c")
        try:
            prop.choices = set(["a", "b"])
            self.fail("Assigning choices not containing current value should not be allowed.")
        except model.OutOfBoundError:
            pass # as it should be
        
        try:
            prop.choices = ("a", "b")
            self.fail("Choices should be allowed only if it's a set.")
        except model.InvalidTypeError:
            pass # as it should be
        
        prop.unsubscribe(self.callback_test_notify)
        
        assert(self.called == 1)


class FloatContinuous(model.FloatProperty, model.Continuous):
    """
    A simple class which is both floating and continuous
    """
    def __init__(self, value=0.0, vrange=[]):
        model.Continuous.__init__(self, vrange)
        model.FloatProperty.__init__(self, value)

    def _set(self, value):
        # order is important
        model.Continuous._set(self, value)
        model.FloatProperty._set(self, value)

class StringEnumerated(model.StringProperty, model.Enumerated):
    """
    A simple class which is both string and Enumerated
    """
    def __init__(self, value, choices):
        model.Enumerated.__init__(self, choices)
        model.StringProperty.__init__(self, value)

    def _set(self, value):
        # order is important
        model.Enumerated._set(self, value)
        model.StringProperty._set(self, value)


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()