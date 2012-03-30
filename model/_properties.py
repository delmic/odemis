# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

class InvalidTypeError(Exception):
    pass

class OutOfBoundError(Exception):
    pass

class Property(object):
    '''
    A property represents a value (an object) with:
     * meta-information (min, max, unit, read-only...)
     * observable behaviour (any one can ask to be notified when the value changes) 
    '''

    def __init__(self, initval, unit=""):
        """
        Creates a property with a given initial value
        initval : any type
        unit (str): a SI unit in which the property is expressed
        """
        # TODO make it a weakref to automatically update the set when a listener
        # goes away. See pypubsub weakmethod.py or http://mindtrove.info/python-weak-references/
#        self._listeners = weakref.WeakSet()
        self._listeners = set()
        
        self._set(initval)
        self.unit = unit
        
    @property
    def value(self):
        """The value of this property"""
        return self._value
    
    def _set(self, value):
        """
        Override to do checking on the value.
        """
        self._value = value

    @value.setter
    def value(self, value):
        prev_value = self._value
        self._set(value)
        if prev_value != self._value:
            self.notify()
    
    @value.deleter
    def value(self):
        del self._value
        
    def subscribe(self, listener, init=False):
        """
        Register a callback function to be called when the ActiveValue is 
        listener (function): callback function which takes as argument val the new value
        init (boolean): if True calls the listener directly, to initialise it
        """
        assert callable(listener)
        self._listeners.add(listener)
        
        if init:
            listener(self.value)
            
        # TODO allow to pass custom additional parameters to the callback 

    def unsubscribe(self, listener):
        self._listeners.discard(listener)

    def notify(self):
        for l in self._listeners:
            l(self.value)


class StringProperty(Property):
    """
    A property which contains a string
    """
    
    def __init__(self, value=""):
        Property.__init__(self, value)
        
    def _set(self, value):
        if not isinstance(value, basestring):
            raise InvalidTypeError("Value '%s' is not a string." % str(value))
        Property._set(self, value)

class FloatProperty(Property):
    """
    A property which contains a float
    """
    
    def __init__(self, value=0.0):
        Property.__init__(self, value)
        
    def _set(self, value):
        try:
            converted = float(value)
        except ValueError:
            raise InvalidTypeError("Value '%s' is not a float." % str(value))
        Property._set(self, converted)

class IntProperty(Property):
    """
    A property which contains a float
    """
    
    def __init__(self, value=0):
        Property.__init__(self, value)
        
    def _set(self, value):
        # we really accept only int, to avoid hiding lose of precision
        if not isinstance(value, int):
            raise InvalidTypeError("Value '%s' is not a int." % str(value))
        Property._set(self, value)

class ListProperty(Property):
    """
    A property which contains a list of values
    """
    
    def __init__(self, value=[]):
        Property.__init__(self, value)
        
    def _set(self, value):
        if not isinstance(value, list):
            raise InvalidTypeError("Value '%s' is not a list." % str(value))
        # TODO we need to also detect whenever this list is modified
        
        Property._set(self, value)


class Continuous(object):
    """
    Adds the ability to a property to specify a min and max.
    It has an attribute range (2-tuple) min, max
    It checks that any value set is min <= val <= max
    """
    
    def __init__(self, vrange):
        """
        range (2-tuple)
        """
        self._set_range(vrange)
    
    @property
    def range(self):
        """The range within which the value of the property can be"""
        return self._range
    
    def _set_range(self, new_range):
        """
        Override to do more checking on the range.
        """
        if len(new_range) != 2:
                raise InvalidTypeError("Range '%s' is not a 2-tuple." % str(new_range))
        if new_range[0] > new_range[1]:
            raise InvalidTypeError("Range min (%s) should be smaller than max (%s)." 
                                   % (str(new_range[0]), str(new_range[1])))
        if hasattr(self, "value"):
            if self.value < new_range[0] or self.value > new_range[1]:
                raise OutOfBoundError("Current value '%s' is outside of the range %s-%s." % 
                            (str(self.value), str(new_range[0]), str(new_range[1])))
        self._range = tuple(new_range)

    @range.setter
    def range(self, value):
        self._set_range(value)
    
    @range.deleter
    def range(self):
        del self._range

    def _set(self, value):
        """
        Should be called _in addition_ to the ._set() of Property
        returns nothing
        Raises:
            OutOfBoundError if the value is not within the authorised range
        """
        # we consider that the subclass has member .value
        if value < self._range[0] or value > self._range[1]:
            raise OutOfBoundError("Trying to assign value '%s' outside of the range %s-%s." % 
                        (str(value), str(self._range[0]), str(self._range[1])))

class Enumerated(object):
    """
    Adds the ability to a property to specify a set of authorised values.
    It has an attribute choices which is of type set
    It checks that any value set is among choice
    """

    def __init__(self, choices):
        """
        choices (seq): all the possible value that can be assigned
        """
        self._set_choices(choices)
        
    def _set(self, value):
        if not value in self._choices:
            raise OutOfBoundError("Value '%s' is not part of possible choices: %s." % 
                        (str(value), ", ".join(map(str, self._choices))))
    
    def _set_choices(self, new_choices):
        if not isinstance(new_choices, set):
            raise InvalidTypeError("choices attribute '%s' is not a set." %  str(new_choices))
        if hasattr(self, "value"):
            if not self.value in new_choices:
                raise OutOfBoundError("Current value '%s' is not part of possible choices: %s." % 
                            (str(self.value), ", ".join(map(str, new_choices))))
        self._choices = new_choices    
    
    @property
    def choices(self):
        return self._choices
    
    @choices.setter
    def choices(self, value):
        self._set_choices(value)
    
    @choices.deleter
    def choices(self):
        del self._choices
    
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
