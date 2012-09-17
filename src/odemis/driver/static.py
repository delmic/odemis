# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis import __version__
from odemis import model
import collections

"""
Provides various components which are not actually drivers but just representing
physical components which cannot be modified by software. It's mostly used for
computing the right metadata/behaviour of the system.
"""

# TODO what is the best type? Emitter? Or something else? 
# Detector needs a specific .data and .shape 
class OpticalLens(model.HwComponent):
    """
    A very simple class which just represent a lens with a given magnification.
    It should "affect" the detector on which it's in front of.
    """
    def __init__(self, name, role, mag, **kwargs):
        """
        name (string): should be the name of the product (for metadata)
        mag (float > 0): magnification ratio
        """
        assert (mag > 0)
        model.HwComponent.__init__(self, name, role, **kwargs)
        
        self._swVersion = "N/A (Odemis %s)" % __version__.version
        self._hwVersion = name
        
        # allow the user to modify the value, if the lens is manually changed
        self.magnification = model.FloatContinuous(mag, range=[1e-3, 1e6], unit="")
    
    
class LightFilter(model.HwComponent):
    """
    A very simple class which just represent a light filter (blocks a wavelength 
    band).
    It should "affect" the detector on which it's in front of, if it's filtering
    the "out" path, or "affect" the emitter in which it's after, if it's
    filtering the "in" path.
    """
    def __init__(self, name, role, band, **kwargs):
        """
        name (string): should be the name of the product (for metadata)
        band ((list of) 2-tuple of float > 0): (m) lower and higher bound of the
          wavelength of the light which goes _through_. If it's a list, it implies
          that the filter is multi-band.
        """
        model.HwComponent.__init__(self, name, role, **kwargs)

        self._swVersion = "N/A (Odemis %s)" % __version__.version
        self._hwVersion = name
        
        # Create a 2-tuple or a set of 2-tuples 
        if not isinstance(band, collections.Iterable) or len(band) == 0:
            raise TypeError("band must be a (list of a) list of 2 floats")
        # is it a list of list?
        if isinstance(band[0], collections.Iterable):
            # => set of 2-tuples
            new_band = []
            for sb in band:
                if len(sb) != 2:
                    raise TypeError("Expected only 2 floats in band, found %d" % len(sb))
                if sb[0] > sb[1]:
                    raise TypeError("Min of band must be first in list")
                new_band.append(tuple(sb))
            band = frozenset(new_band)
        else:
            # 2-tuple
            if len(band) != 2:
                raise TypeError("Expected only 2 floats in band, found %d" % len(band))
            if band[0] > band[1]:
                raise TypeError("Min of band must be first in list")
            band = [tuple(band)]
        
        # Check that the values are in m: they are typically within nm (< um!)
        max_val = 1e-6
        for low, high in band:
            if low > max_val or high > max_val:
                raise ValueError("Band contains very high values for light wavelength, ensure the value is in meters: %r.", band)
        
        self.band = model.ListVA(band, unit="m")
        
        # TODO: MD_OUT_WL or MD_IN_WL depending on affect
        self._metadata = {model.MD_FILTER_NAME: name,
                          model.MD_OUT_WL: band}
        
    def getMetadata(self):
        return self._metadata
    
