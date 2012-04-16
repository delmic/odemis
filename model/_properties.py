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
import weakref

class InvalidTypeError(Exception):
    pass

class OutOfBoundError(Exception):
    pass

class NotSettableError(Exception):
    pass

class WeakRefLostError(Exception):
    pass

class Property(object):
    '''
    A property represents a value (an object) with:
     * meta-information (min, max, unit, read-only...)
     * observable behaviour (any one can ask to be notified when the value changes) 
    '''

    def __init__(self, initval, unit="", readonly=False):
        """
        Creates a property with a given initial value
        initval : any type
        unit (str): a SI unit in which the property is expressed
        readonly (bool): if True, value setter will raise an exception. It's still
            possible to change the value by calling _set() and then notify()
        """
        # TODO shall we have a assigner callback which is called to set the value and returns the actual value (for hardware)
        self._listeners = set()
        
        self._set(initval)
        self.unit = unit
        self.readonly = readonly
        
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
        if self.readonly:
            raise NotSettableError("Value is readonly")
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
        self._listeners.add(WeakMethod(listener))
        
        if init:
            listener(self.value)
            
        # TODO allow to pass custom additional parameters to the callback 

    def unsubscribe(self, listener):
        self._listeners.discard(WeakMethod(listener))

    def notify(self):
        for l in self._listeners.copy():
            try:
                l(self.value)
            except WeakRefLostError:
                self.unsubscribe(l)

class StringProperty(Property):
    """
    A property which contains a string
    """
    
    def __init__(self, value="", unit="", readonly=False):
        Property.__init__(self, value, unit, readonly)
        
    def _set(self, value):
        if not isinstance(value, basestring):
            raise InvalidTypeError("Value '%s' is not a string." % str(value))
        Property._set(self, value)

class FloatProperty(Property):
    """
    A property which contains a float
    """
    
    def __init__(self, value=0.0, unit="", readonly=False):
        Property.__init__(self, value, unit, readonly)
        
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
    
    def __init__(self, value=0, unit="", readonly=False):
        Property.__init__(self, value, unit, readonly)
        
    def _set(self, value):
        # we really accept only int, to avoid hiding lose of precision
        if not isinstance(value, int):
            raise InvalidTypeError("Value '%s' is not a int." % str(value))
        Property._set(self, value)

class ListProperty(Property):
    """
    A property which contains a list of values
    """
    
    def __init__(self, value=[], unit="", readonly=False):
        Property.__init__(self, value, unit, readonly)
        
    def _set(self, value):
        try:
            converted = list(value)
        except TypeError:
            raise InvalidTypeError("Value '%s' is not a list." % str(value))
        # TODO we need to also detect whenever this list is modified
        
        Property._set(self, converted)

# TODO maybe should provide a factory that can take a Property class and return it
# either Continuous or Enumerated

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
    
    def _set_choices(self, new_choices_raw):
        try:
            new_choices = frozenset(new_choices_raw)
        except TypeError:
            raise InvalidTypeError("Choices '%s' is not a set." % str(new_choices_raw))
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


class FloatContinuous(FloatProperty, Continuous):
    """
    A simple class which is both floating and continuous
    """
    def __init__(self, value=0.0, vrange=[], unit=""):
        Continuous.__init__(self, vrange)
        FloatProperty.__init__(self, value, unit)

    def _set(self, value):
        # order is important
        Continuous._set(self, value)
        FloatProperty._set(self, value)

class StringEnumerated(StringProperty, Enumerated):
    """
    A simple class which is both string and Enumerated
    """
    def __init__(self, value, choices, unit=""):
        Enumerated.__init__(self, choices)
        StringProperty.__init__(self, value, unit)

    def _set(self, value):
        # order is important
        Enumerated._set(self, value)
        StringProperty._set(self, value)

class FloatEnumerated(FloatProperty, Enumerated):
    """
    A simple class which is both floating and enumerated
    """
    def __init__(self, value=0.0, choices=[], unit=""):
        Enumerated.__init__(self, choices)
        FloatProperty.__init__(self, value, unit)

    def _set(self, value):
        # order is important
        Enumerated._set(self, value)
        FloatProperty._set(self, value)

class IntEnumerated(IntProperty, Enumerated):
    """
    A simple class which is both int and enumerated
    """
    def __init__(self, value=0.0, choices=[], unit=""):
        Enumerated.__init__(self, choices)
        IntProperty.__init__(self, value, unit)

    def _set(self, value):
        # order is important
        Enumerated._set(self, value)
        IntProperty._set(self, value)


class MultiSpeedProperty(Property, Continuous):
    """
    A class to define speed (m/s) for several axes
    It's especially made for Actuator.speed: the value is a dict name => float
    Also the speed must be >0
    """
    def __init__(self, value={}, vrange=[], unit="m/s"):
        Continuous.__init__(self, vrange)
        assert(vrange[0] >= 0)
        Property.__init__(self, value, unit)
        
    # TODO detect whenever a value of the dict is changed 
    def _set(self, value):
        # a dict
        if not isinstance(value, dict):
            raise InvalidTypeError("Value '%s' is not a dict." % str(value))
        for axis, v in value.items():
            # It has to be within the range, but also > 0
            if v <= 0 or v < self._range[0] or v > self._range[1]:
                raise OutOfBoundError("Trying to assign axis '%s' value '%s' outside of the range %s-%s." % 
                            (str(axis), str(value), str(self._range[0]), str(self._range[1])))
        Property._set(self, value)

            
class WeakMethodBound(object):
    def __init__(self, f):
        self.f = f.im_func
        self.c = weakref.ref(f.im_self)
        # cache the hash so that it's the same after deref'd
        self.hash = hash(f.im_func) + hash(f.im_self)
        
    def __call__(self, *arg, **kwargs):
        ins = self.c() 
        if ins == None:
            raise WeakRefLostError, 'Method called on dead object'
        return self.f(ins, *arg, **kwargs)
        
    def __hash__(self):
        return self.hash
    
    def __eq__(self, other):
        try:
            return (type(self) is type(other) and self.f == other.f
                    and self.c() == other.c())
        except:
            return False
        
#    def __ne__(self, other):
#        return not self == other

class WeakMethodFree(object):
    def __init__(self, f):
        self.f = weakref.ref(f)
        # cache the hash so that it's the same after deref'd
        self.hash = hash(f)
        
    def __call__(self, *arg, **kwargs):
        fun = self.f()
        if fun == None:
            raise WeakRefLostError, 'Function no longer exist'
        return fun(*arg, **kwargs)
        
    def __hash__(self):
        return self.hash
    
    def __eq__(self, other):
        try:
            return type(self) is type(other) and self.f() == other.f()
        except:
            return False
        
#    def __ne__(self, other):
#        return not self == other

def WeakMethod(f):
    try:
        f.im_func
    except AttributeError:
        return WeakMethodFree(f)
    return WeakMethodBound(f)
 
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
