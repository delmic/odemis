#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 16 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

class ActiveValue(object):
    '''
    An active value is a value (an object) that can let every one know when it 
    has been modified (actually modified, so putting the same value doesn't cause notification)
    '''

    def __init__(self, initval):
        """
        Creates an active value with a given initial value
        initval : any type
        """
        # TODO make it a weakref to automatically update the set when a listener
        # goes away. See pypubsub weakmethod.py or http://mindtrove.info/python-weak-references/
#        self._listeners = weakref.WeakSet()
        self._listeners = set()
        self._set(initval)
        
    def bind(self, listener):
        """
        Register a callback function to be called when the ActiveValue is 
        listener (function): callback function which takes as argument val the new value
        """
        assert callable(listener)
        self._listeners.add(listener)

    def unbind(self, listener):
        self._listeners.discard(listener)

    def notify(self):
        for l in self._listeners:
            l(self.value)
    
    def _set(self, value):
        """
        Override to do checking on the value.
        """
        object.__setattr__(self, "value", value)
    
    def __setattr__(self, name, value):
        if name == "value":
            prev_value = self.value
            self._set(value)
            if prev_value != self.value:
                self.notify()
        else:
            object.__setattr__(self, name, value)
            
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: