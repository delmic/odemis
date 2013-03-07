# -*- coding: utf-8 -*-
'''
Created on 6 Mar 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS F

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division
from odemis import model
from odemis.model._components import ComponentBase, ArgumentError
from odemis.model._dataflow import DataFlowBase
import logging

# This is a spectrometer class that strives to be generic by representing a 
# spectrometer out of just a generic DigitalCamera and actuator which offers 
# a wavelength dimension.
# So far, it might not be that generic, because it's only tested with a 
# SpectraPro and PI PVCam.


class CompositedSpectrometer(model.Detector):
    '''
    A generic Detector which takes 2 children to create a spectrometer. It's
    essentially a wrapper to a DigitalCamera to generate a spectrum as data
    from the DataFlow. Manipulation of the mirrors/gratings/prism must be done
    via the "spectrograph" child. On the contrary, access to the detector must
    be done only via this Component, and never directly on the "detector" child.
    
    The main differences between a Spectrometer and a normal DigitalCamera are:
     * the spectrometer data flow has only one dimension (i.e., second dimension
       is fixed to 1)
     * the metadata has an additional entry MD_WL_POLYNOMIAL which allows to
       convert from the pixel coordinates to the wavelengths.
     * the maximum can be bigger than the maximum resolution (but not of the 
       shape).
    '''
# Note that the dataflow always gives data of 1 dimension, and so .resolution
# must also be always of 1 dimension. .shape can be bigger than that.

# TODO: is the API meaningful? Is it really the most obvious way to represent a
# spectrometer component by having a Detector and a _child_ for changing the 
# optics settings? How about just inheriting both from actuator and detector?

    def __init__(self, name, role, children, **kwargs):
        '''
        children (dict string->model.HwComponent): the children
            There must be exactly two children "spectrograph" and "detector". The 
            first dimension of the CCD is supposed to be along the wavelength,
            with the first pixels representing the lowest wavelengths. 
        Raise an ArgumentError exception if the children are not compatible
        '''
        # we will fill the set of children with Components later in ._children 
        model.Detector.__init__(self, name, role, **kwargs)
        
        # Check the children
        dt = children["detector"]
        if not isinstance(dt, ComponentBase):
            raise ArgumentError("Child detector is not a component.")
        if not hasattr(dt, "shape") or not isinstance(dt.shape, tuple):
            raise ArgumentError("Child detector is not a Detector component.")
        if not hasattr(dt, "data") or not isinstance(dt.data, DataFlowBase):
            raise ArgumentError("Child detector is not a Detector component.")
        self._detector = dt
        
        sp = children["spectrograph"]
        if not isinstance(sp, ComponentBase):
            raise ArgumentError("Child spectrograph is not a component.")
        try:
            if not "wavelength" in sp.axes:
                raise ArgumentError("Child spectrograph has no 'wavelength' axis.")
        except Exception:
            raise ArgumentError("Child spectrograph is not an Actuator.")
        self._spectrograph = sp

        # set up the detector part
        # check that the shape is "horizontal"
        if dt.shape[0] <= 1:
            raise ArgumentError("Child detector must have at least 2 pixels horizontally")
        if dt.shape[0] < dt.shape[1]:
            logging.warning("Child detector is shaped vertically (%dx%d), "
                            "this is probably incorrect, as wavelengths are " 
                            "expected to be along the horizontal axis", 
                            dt.shape[0], dt.shape[1])
        # shape is same as detector (raw sensor), but the max resolution is always flat
        self._shape = tuple(dt.shape) # duplicate
        
        # The resolution and binning are derived from the detector, but with 
        # settings set so that there is only one horizontal line.
        
        # TODO: give a init parameter or VA to specify a smaller window height
        # than the entire CCD (some spectrometers have only noise on the top and
        # bottom)
        if dt.binning.range[1][1] < dt.resolution.range[1][1]:
            # without software binning, we are stuck to the max binning
            logging.info("Spectrometer %s will only use a %d px band of the %d "
                         "px of the sensor", name, dt.binning.range[1][1],
                         dt.resolution.range[1][1])
        
        resolution = [dt.resolution.range[1][0], 1] # max,1
        binning = [1, 1]
        # horizontally: as fine as possible, with a maximum around 256px, over
        #  this, use binning if possible
        binning[0] = min(max(resolution[0] // 256,
                             dt.binning.range[0][0]), dt.binning.range[1][0])
        resolution[0] //= binning[0]
         
        # vertically: 1, with binning as big as possible
        binning[1] = min(dt.binning.range[1][1], dt.resolution.range[1][1]) 
        
        min_res = (dt.resolution.range[0][0], 1)
        max_res = (dt.resolution.range[1][0], 1)
        self._resolution = resolution
        self.resolution = model.ResolutionVA(resolution, [min_res, max_res], 
                                             setter=self._setResolution)
        # 2D binning is like a "small resolution"
        self._binning = binning
        self.binning = model.ResolutionVA(self._binning, dt.binning.range,
                                          setter=self._setBinning)
   
        self._setBinning(binning) # will also update the resolution
        
        # TODO: support software binning by rolling up our own dataflow that
        # does data merging
        assert (dt.resolution.range[0][1] == 1)
        self.data = dt.data
        
        # duplicate every other VA from the detector
        for aname, value in model.getVAs(dt).items():
            if not hasattr(self, aname):
                setattr(self, aname, value)
            else:
                logging.debug("skipping duplication of already existing VA '%s'", aname)


        # TODO: update Metadata of detector with wavelength polynomial conversion 
        # whenever the wavelength axis moves. 
    
    def _setBinning(self, value):
        # setting resolution and binning is slightly tricky, because binning
        # will change resolution to keep the same area. So first set binning, then
        # resolution
        self._detector.binning.value = value
        # TODO: update the resolution so that the ROI stays the same??
        self._detector.resolution.value = self.resolution.value
        return value