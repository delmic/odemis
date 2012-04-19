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
import model
import numpy

"""
Provides data-flow: an object that can contain a large array of data regularly
updated. Typically it is used to transmit video (sequence of images). It does it
losslessly and with metadata attached.
"""

# This list of constants are used as key for the metadata
MD_EXP_TIME = "Exposure time" # s
MD_ACQ_DATE = "Acquisition date" # s since epoch
# distance between two points on the sample that are seen at the centre of two
# adjacent pixels considering that these two points are in focus 
MD_PIXEL_SIZE = "Pixel size" # (m, m)  
MD_BINNING = "Binning" # px
MD_HW_VERSION = "Hardware version" # str
MD_SW_VERSION = "Software version" # str
MD_HW_NAME = "Hardware name" # str, product name of the hardware component (and s/n)
MD_GAIN = "Gain" # no unit (ratio)
MD_BPP = "Bits per pixel" # bit
MD_READOUT_TIME = "Pixel readout time" # s, time to read one pixel
MD_SENSOR_PIXEL_SIZE = "Sensor pixel size" # (m, m), distance between the center of 2 pixels on the detector sensor
MD_SENSOR_SIZE = "Sensor size" # px, px
MD_SENSOR_TEMP = "Sensor temperaure" # C
MD_POS = "Centre position" # (m, m), location of the picture centre relative to top-left of the sample)
MD_IN_WL = "Input wavelength range" # (m, m), lower and upper range of the wavelenth input
MD_OUT_WL = "Output wavelength range"  # (m, m), lower and upper range of the filtered wavelenth before the camera
MD_LIGHT_POWER = "Light power" # W, power of the emitting light

class DataArray(numpy.ndarray):
    """
    Array of data (a numpy nd.array) + metadata.
    It is the main object returned by a dataflow.
    It can be created either explicitly:
     DataArray([2,3,1,0], metadata={"key": 2})
    or via a view:
     x = numpy.array([2,3,1,0])
     x.view(DataArray)
    """
    
    # see http://docs.scipy.org/doc/numpy/user/basics.subclassing.html
    def __new__(cls, input_array, metadata={}):
        """
        input_array: array from which to initialise the data
        metadata (dict str-> value): a dict of (standard) names to their values
        """
        obj = numpy.asarray(input_array).view(cls)
        obj.metadata = metadata
        return obj

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.metadata = getattr(obj, 'metadata', {})

class DataFlow(object):
    """
    This is an abstract class that must be extended by each detector which
    wants to provide a dataflow.
    extend: subscribe() and unsubcribe() to start stop generating data. 
            Each time a new data is available it should call notify(dataarray)
    extend: get() to synchronously return the next dataarray available
    """
    def __init__(self):
        self._listeners = set()
        
        # to be overridden
        self.parent = None
        
    def get(self):
        # TODO timeout argument?
        pass
    
    def subscribe(self, listener):
        """
        Register a callback function to be called when the ActiveValue is 
        listener (function): callback function which takes as arguments 
           dataflow (this object) and data (the new data array)
        """
        # TODO update rate argument to indicate how often we need an update?
        assert callable(listener)
        self._listeners.add(model.WeakMethod(listener))
        
    def unsubscribe(self, listener):
        self._listeners.discard(model.WeakMethod(listener))

    def notify(self, data):
        """
        data (DataArray): the data to be sent to listeners
        """
        assert(isinstance(data, DataArray))
        
        # TODO this might have to be moved to the backend whenever sending new
        # data to clients
        model.updateMetadata(data.metadata, self.parent)
        
        for l in self._listeners.copy(): # to allow modify the set while calling
            try:
                l(self, data)
            except model.WeakRefLostError:
                self.unsubscribe(l)
            
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
