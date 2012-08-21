# -*- coding: utf-8 -*-
'''
Created on 20 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from odemis import model
import logging


class MetadataUpdater(model.Component):
    '''
    Takes care of updating the metadata of detectors, based on the physical
    attributes of other components in the system.
    This implementation is specific to microscopes. 
    '''
    # This is kept in a separate module from the main backend because it has to 
    # know the business semantic. 

    def __init__(self, name, microscope, components, **kwargs):
        '''
        microscope (model.Microscope): the microscope to observe and update
        components (set of model.Components): all the components of the system
        '''
        # Warning: for efficiency, we want to run in the same container as the back-end
        # but this means the back-end is not running yet when we are created
        # so we cannot access the back-end.
        
        # list of 2-tuples (function, *arg): to be called on terminate
        self._onTerminate = []
        self._components = components
        
        model.Component.__init__(self, name, **kwargs)
        
        # For each detector
        # Find other components that affects it (according to their role)
        # Subscribe to the changes of the attributes that matter
        for d in microscope.detectors:
            for a in self._getAffecting(d):
                if a.role == "stage":
                    # update the image position
                    self.observeStage(a, d)
                    #TODO : support more metadata
#                elif a.role == "focus":
#                    # update the image focus
#                    self.observeFocus(a, d)
                elif a.role == "lens":
                    # update the pixel size
                    self.observeLens(a, d)
#                elif a.role == "filter":
#                    # update the received light wavelength
#                    self.observeFilter(a, d)
#                elif a.role == "light":
#                    # update the emitted light wavelength
#                    self.observeLight(a, d)
    
    def _getAffecting(self, affected):
        """
        Returns all the components that affect a given component
        affected (Component): component that is affected
        returns (list of Components): the components affecting "affected"
        """
        affectings = []
        for c in self._components:
            if affected in c.affects:
                affectings.append(c)
        
        return affectings
    
    def observeStage(self, stage, detector):
        # we need to keep the information on the detector to update
        def updateStagePos(pos):
            # We need axes X and Y
            if not "x" in pos or not "y" in pos:
                logging.warning("Stage position doesn't contain X/Y axes")
            # if unknown, just assume a fixed position
            x = pos.get("x", 0)
            y = pos.get("y", 0)
            md = {model.MD_POS: (x, y)}
            detector.updateMetadata(md)
        
        stage.position.subscribe(updateStagePos)
        updateStagePos(stage.position.value)
        self._onTerminate.append((stage.position.unsubscribe, (updateStagePos,)))

    def observeLens(self, lens, detector):
        if detector.role != "ccd":
            logging.warning("Does not know what to do with a lens in front of a %s", detector.role)
            return
        
        # Depends on the actual size of the ccd's density (should be constant)
        captor_mpp = detector.pixelSize.value # m, m
        
        # update static information
        md = {model.MD_LENS_NAME: lens.hwVersion}
        detector.updateMetadata(md)
        
        # we need to keep the information on the detector to update
        def updatePixelDensity(mag):
            # the formula is very simple: actual MpP = CCD MpP / Mag
            mpp = (captor_mpp[0] / mag, captor_mpp[1] / mag) 
            md = {model.MD_PIXEL_SIZE: mpp}
            detector.updateMetadata(md)
        
        lens.magnification.subscribe(updatePixelDensity)
        updatePixelDensity(lens.magnification.value) # update it right now
        self._onTerminate.append((lens.magnification.unsubscribe, (updatePixelDensity,)))
    
    def terminate(self):
        # call all the unsubscribes
        for fun, args in self._onTerminate:
            try:
                fun(*args)
            except:
                logging.exception("Failed to unsubscribe metadata properly.")

        model.Component.terminate(self)