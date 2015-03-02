# -*- coding: utf-8 -*-
'''
Created on 6 Mar 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division
from numpy.polynomial import polynomial
from odemis import model
from odemis.model import ComponentBase, DataFlowBase
import logging
import math

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
        Raise an ValueError exception if the children are not compatible
        '''
        # we will fill the set of children with Components later in ._children 
        model.Detector.__init__(self, name, role, **kwargs)
        
        # Check the children
        dt = children["detector"]
        if not isinstance(dt, ComponentBase):
            raise ValueError("Child detector is not a component.")
        if not hasattr(dt, "shape") or not isinstance(dt.shape, tuple):
            raise ValueError("Child detector is not a Detector component.")
        if not hasattr(dt, "data") or not isinstance(dt.data, DataFlowBase):
            raise ValueError("Child detector is not a Detector component.")
        self._detector = dt
        self.children.value.add(dt)
        
        sp = children["spectrograph"]
        if not isinstance(sp, ComponentBase):
            raise ValueError("Child spectrograph is not a component.")
        try:
            if not "wavelength" in sp.axes:
                raise ValueError("Child spectrograph has no 'wavelength' axis.")
        except Exception:
            raise ValueError("Child spectrograph is not an Actuator.")
        self._spectrograph = sp
        self.children.value.add(sp)

        # set up the detector part
        # check that the shape is "horizontal"
        if dt.shape[0] <= 1:
            raise ValueError("Child detector must have at least 2 pixels horizontally")
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
        self.resolution = model.ResolutionVA(resolution, [min_res, max_res], 
                                             setter=self._setResolution)
        # 2D binning is like a "small resolution"
        self._binning = tuple(binning)
        self.binning = model.ResolutionVA(self._binning, dt.binning.range,
                                          setter=self._setBinning)
   
        self._setBinning(self._binning) # will also update the resolution
        
        # TODO: support software binning by rolling up our own dataflow that
        # does data merging
        assert dt.resolution.range[0][1] == 1
        self.data = dt.data
        
        # duplicate every other VA and Event from the detector
        # that includes required VAs like .pixelSize and .exposureTime
        for aname, value in model.getVAs(dt).items() + model.getEvents(dt).items():
            if not hasattr(self, aname):
                setattr(self, aname, value)
            else:
                logging.debug("skipping duplication of already existing VA '%s'", aname)

        assert hasattr(self, "pixelSize")
        assert hasattr(self, "exposureTime")

        # Update metadata of detector with wavelength conversion 
        # whenever the wavelength/grating axes moves.
        try:
            self._pn_phys = sp.getPolyToWavelength()
        except AttributeError:
            raise ValueError("Child spectrograph has no getPolyToWavelength() method")

        sp.position.subscribe(self._onPositionUpdate)
        self.resolution.subscribe(self._onResBinningUpdate)
        self.binning.subscribe(self._onResBinningUpdate, init=True) 
    
    # The following 2 metadata methods are just redirecting to the detector
    def getMetadata(self):
        return self._detector.getMetadata()
    
    def updateMetadata(self, md):
        """
        Update the metadata associated with every image acquired to these
        new values. It's accumulative, so previous metadata values will be kept
        if they are not given.
        md (dict string -> value): the metadata
        """
        self._detector.updateMetadata(md)
    
    def _onPositionUpdate(self, pos):
        """
        Called when the wavelength position or grating (ie, groove density) 
          of the spectrograph is changed.
        """
        # Need to get new conversion polynomial and update metadata
        self._pn_phys = self._spectrograph.getPolyToWavelength()
        self._updateWavelengthPolynomial()
        
    def _onResBinningUpdate(self, value):
        self._updateWavelengthPolynomial()
        
    def _updateWavelengthPolynomial(self):
        """
        Update the metadata with the wavelength conversion polynomial provided
        by the spectrograph. Should be called every time ._pn_phys is updated,
        or whenever the binning or resolution change
        """
        # This polynomial is from m (distance from centre) to m (wavelength),
        # but we need from px (pixel number on spectrum) to m (wavelength). So
        # we need to convert by using the density and quantity of pixels
        # wl = pn(x)
        # x = a + bx' = pn1(x')
        # wl = pn(pn1(x')) = pnc(x')
        # => composition of polynomials
        # with "a" the distance of the centre of the left-most pixel to the 
        # centre of the image, and b the density in meters per pixel. 
        
        mpp = self.pixelSize.value[0] * self._binning[0] # m/px
        # distance from the pixel 0 to the centre (in m)
        distance0 = -(self.resolution.value[0] / 2 - 0.5) * mpp
        pnc = self.polycomp(self._pn_phys, [distance0, mpp])
        
        md = {model.MD_WL_POLYNOMIAL: pnc}
        self.updateMetadata(md)
    
    @staticmethod
    def polycomp(c1, c2):
        """
        Compose two polynomials : c1 o c2 = c1(c2(x))
        The arguments are sequences of coefficients, from lowest order term to highest, e.g., [1,2,3] represents the polynomial 1 + 2*x + 3*x**2.
        """
        # TODO: Polynomial(Polynomial()) seems to do just that?
        # using Horner's method to compute the result of a polynomial
        cr = [c1[-1]]
        for a in reversed(c1[:-1]):
            # cr = cr * c2 + a 
            cr = polynomial.polyadd(polynomial.polymul(cr, c2), [a])
        
        return cr
    
    def _setBinning(self, value):
        """
        Called when "binning" VA is modified. It also updates the resolution so
        that the horizontal AOI is approximately the same. The vertical size
        stays 1.
        value (int): how many pixels horizontally and vertically
          are combined to create "super pixels"
        """
        prev_binning = self._binning
        self._binning = tuple(value) # duplicate
        
        # adapt horizontal resolution so that the AOI stays the same
        changeh = prev_binning[0] / self._binning[0]
        old_resolution = self.resolution.value
        assert old_resolution[1] == 1
        new_resh = int(round(old_resolution[0] * changeh))
        new_resh = max(min(new_resh, self.resolution.range[1][0]), self.resolution.range[0][0])
        new_resolution = (new_resh, 1)
        
        # setting resolution and binning is slightly tricky, because binning
        # will change resolution to keep the same area. So first set binning, then
        # resolution
        self._detector.binning.value = value
        self.resolution.value = new_resolution
        return value
    
    def _setResolution(self, value):
        """
        Called when the resolution VA is to be updated.
        """
        # only the width might change
        assert value[1] == 1
        
        # fit the width to the maximum possible given the binning
        max_size = int(self.resolution.range[1][0] // self._binning[0])
        min_size = int(math.ceil(self.resolution.range[0][0] / self._binning[0]))
        size = (max(min(value[0], max_size), min_size), 1)
        
        self._detector.resolution.value = size
        assert self._detector.resolution.value[1] == 1 # TODO: handle this by software mean
        
        return size

    def selfTest(self):
        return self._detector.selfTest() and self._spectrograph.selfTest()
    
    # No scan(): we cannot detect if a detector and spectrograph are linked or 
    # not without endangering the system too much (e.g., it's not clever to move
    # the spectrograph like crazy while acquiring images from all CCDs)
