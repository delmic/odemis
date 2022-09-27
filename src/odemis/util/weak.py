# -*- coding: utf-8 -*-
'''
Created on 2 Jun 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Special functions and class to manage method/function with weakref
# wxpython.pubsub has something similar
import weakref


class WeakRefLostError(Exception):
    pass

class WeakMethodBound(object):
    def __init__(self, f):
        self.f = f.__func__
        self.c = weakref.ref(f.__self__)
        # cache the hash so that it's the same after deref'd
        self.hash = hash(f.__func__) + hash(f.__self__)

    def __call__(self, *arg, **kwargs):
        ins = self.c()
        if ins is None:
            raise WeakRefLostError('Method called on dead object')
        return self.f(ins, *arg, **kwargs)

    def __hash__(self):
        return self.hash

    def __eq__(self, other):
        try:
            return (type(self) is type(other) and self.f == other.f
                    and self.c() == other.c())
        except:
            return False

    # def __ne__(self, other):
    #     return not self == other

class WeakMethodFree(object):
    def __init__(self, f):
        self.f = weakref.ref(f)
        # cache the hash so that it's the same after deref'd
        self.hash = hash(f)

    def __call__(self, *arg, **kwargs):
        fun = self.f()
        if fun is None:
            raise WeakRefLostError('Function no longer exist')
        return fun(*arg, **kwargs)

    def __hash__(self):
        return self.hash

    def __eq__(self, other):
        try:
            return type(self) is type(other) and self.f() == other.f()
        except:
            return False

    # def __ne__(self, other):
    #    return not self == other

def WeakMethod(f):
    try:
        # Check if the parameter has a function object, which is the case
        # if it's a bound function (ie.e a method)
        f.__func__
    except AttributeError:
        return WeakMethodFree(f)
    return WeakMethodBound(f)
