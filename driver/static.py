# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from model._core import roattribute
import __version__
import model


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
        self._magnification = mag
        self._metadata = {model.MD_LENS_NAME: name,
                          model.MD_OPT_MAG: mag}
        
    def getMetadata(self):
        return self._metadata
    
    # For info to the user once the component is created
    @roattribute
    def magnigication(self):
        return self._magnification