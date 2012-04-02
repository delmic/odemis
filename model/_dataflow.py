# -*- coding: utf-8 -*-
'''
Created on 2 Apr 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
import numpy

"""
Provides data-flow: an object that can contain a large array of data regularly
updated. Typically it is used to transmit video (sequence of images). It does it
losslessly and with metadata attached.
"""

class DataArray(numpy.ndarray):
    """
    Array of data (a numpy nd.array) + metadata.
    It is the main object returned by a dataflow.
    """
    
    # see http://docs.scipy.org/doc/numpy/user/basics.subclassing.html
    def __new__(cls, shape, dtype=float, buffer=None, offset=0,
          strides=None, order=None, metadata=None):
        """
        metadata (dict str-> value): a dict of (standard) names to their values
        """
        obj = numpy.ndarray.__new__(cls, shape, dtype, buffer, offset, strides, order)
        obj.metadata = metadata
        return obj

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.metadata = getattr(obj, 'metadata', None)

class DataFlow(object):
    """
    This is an abstract class that must be extended by each detector which
    wants to provide a dataflow.
    extend: subscribe() and unsubcribe() to start stop generating data. 
            Each time a new data is available it should call notify(dataarray)
    extend: get() to synchronously return the next dataarray available
    """
    
    def __init__(self):
        # TODO make it a weakref to automatically update the set when a listener
        # goes away. See pypubsub weakmethod.py or http://mindtrove.info/python-weak-references/
#        self._listeners = weakref.WeakSet()
        self._listeners = set()
        
    def get(self):
        # TODO timeout argument?
        pass
    
    def subscribe(self, listener):
        """
        Register a callback function to be called when the ActiveValue is 
        listener (function): callback function which takes as argument data the new data
        """
        # TODO update rate argument to indicate how often we need an update?
        assert callable(listener)
        self._listeners.add(listener)
        
    def unsubscribe(self, listener):
        self._listeners.discard(listener)

    def notify(self, data):
        """
        data (DataArray): the data to be sent to listeners
        """
        assert(isinstance(data, DataArray))
        for l in self._listeners:
            l(data)
            
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
