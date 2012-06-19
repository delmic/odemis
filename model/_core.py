# -*- coding: utf-8 -*-
'''
Created on 18 Jun 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
import weakref

class roattribute(property):
    """
    A member of an object which will be cached in the proxy when remotely shared.
    It can be modified only before the object is ever shared. (Technically, it
    can still be written afterwards but the values will not be synchronised
    between the containers).
    """
    # the implementation is just a (python) property with only a different name
    # TODO force to not have setter, but I have no idea how to, override setter?
    pass

def get_roattributes(self):
    """
    list all roattributes of an instance
    """
#    members = inspect.getmembers(self.__class__)
#    return [name for name, obj in members if isinstance(obj, roattribute)]
    klass = self.__class__
    roattributes = []
    for key in dir(klass):
        try:
            if isinstance(getattr(klass, key), roattribute):
                roattributes.append(key)
        except AttributeError:
            continue
       
    return roattributes

def dump_roattributes(self):
    """
    list all the roattributes and their value
    """
    return dict([[name, getattr(self, name)] for name in get_roattributes(self)])

def load_roattributes(self, roattributes):
    """
    duplicate the given roattributes into the instance.
    useful only for a proxy class
    """
    for a, value in roattributes.items():
        setattr(self, a, value)


# Special functions and class to manage method/function with weakref
# wxpython.pubsub has something similar 

class WeakRefLostError(Exception):
    pass

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
