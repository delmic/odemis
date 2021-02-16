# -*- coding: utf-8 -*-
'''
Created on 18 Dec 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

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
from odemis import model
from odemis.model import ComponentBase


class CompositedScanner(model.Emitter):
    '''
    A generic Emitter which takes 2 dependencies to create a scanner. It's
    essentially a wrapper to an Emitter to generate data using the "external"
    scanner while manipulating HFW, accelerating voltage and probe current via
    the "internal" scanner.
    '''

    def __init__(self, name, role, dependencies, **kwargs):
        '''
        dependencies (dict string->model.HwComponent): the dependencies
            There must be exactly two dependencies "external" and "internal".
        Raise:
          ValueError: if the dependencies are not compatible
        '''
        # we will fill the set of dependencies with Components later in ._dependencies
        model.Emitter.__init__(self, name, role, dependencies=dependencies, **kwargs)

        # Check the dependencies
        extnl = dependencies["external"]
        if not isinstance(extnl, ComponentBase):
            raise ValueError("Dependency external is not a component.")
        if not model.hasVA(extnl, "pixelSize"):
            raise ValueError("Dependency external is not a Emitter component.")
        self._external = extnl

        intnl = dependencies["internal"]
        if not isinstance(intnl, ComponentBase):
            raise ValueError("Dependency internal is not a component.")
        if not hasattr(intnl, "shape"):
            # Note: the internal component doesn't need to provide pixelSize
            raise ValueError("Dependency internal is not a Emitter component.")
        self._internal = intnl

        # Copy VAs directly related to scanning from external
        self._shape = self._external.shape
        for vaname in ("pixelSize", "translation", "resolution", "scale",
                       "dwellTime"):
            if model.hasVA(self._external, vaname):
                va = getattr(self._external, vaname)
                setattr(self, vaname, va)

        # Copy VAs for controlling the ebeam from internal
        # horizontalFoV or magnification need a bit more cleverness
        if model.hasVA(self._internal, "horizontalFoV"):
            self.horizontalFoV = self._internal.horizontalFoV
            # Create read-only magnification VA
            # TODO: why not just using the magnification VA from the internal?
            self.magnification = model.VigilantAttribute(1, unit="", readonly=True)
            self.horizontalFoV.subscribe(self._updateMagnification, init=True)
        elif model.hasVA(self._external, "magnification"):
            self.magnification = self._external.magnification

        # TODO: just pick every VAs which are not yet on self?
        for vaname in ("accelVoltage", "probeCurrent", "depthOfField", "spotSize"):
            if model.hasVA(self._internal, vaname):
                va = getattr(self._internal, vaname)
                setattr(self, vaname, va)

        # VAs that could be both on internal or external. If on both, pick internal
        # TODO: add a better way to select if both provide: either via arg, or
        # select the one which provides a None (=auto), or which is not read-only?
        for vaname in ("power", "blanker", "external", "rotation"):
            if model.hasVA(self._internal, vaname):
                va = getattr(self._internal, vaname)
                setattr(self, vaname, va)
            elif model.hasVA(self._external, vaname):
                va = getattr(self._external, vaname)
                setattr(self, vaname, va)

        # TODO: if blanker has True/False (only), add a None (=auto), which
        # automatically put the underlying value based on the detector acquisition.

    def _updateMagnification(self, hfw):
        new_mag = self._external.HFWNoMag / hfw
        self.magnification._value = new_mag
        self.magnification.notify(new_mag)
        # Also update external magnification
        self._external.magnification.value = new_mag

    # Share the metadata with the external, which is the one that will actually
    # generate the data (with the metadata)
    # TODO: merge the metadata from the internal
    def updateMetadata(self, md):
        self._external.updateMetadata(md)

    def getMetadata(self):
        return self._external.getMetadata()
