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
    A generic Emitter which takes 2 children to create a scanner. It's
    essentially a wrapper to an Emitter to generate data using the "external"
    scanner while manipulating HFW, accelerating voltage and probe current via
    the "internal" scanner.
    '''

    def __init__(self, name, role, children, **kwargs):
        '''
        children (dict string->model.HwComponent): the children
            There must be exactly two children "external" and "internal".
        Raise:
          ValueError: if the children are not compatible
        '''
        # we will fill the set of children with Components later in ._children
        model.Emitter.__init__(self, name, role, **kwargs)

        # Check the children
        extnl = children["external"]
        if not isinstance(extnl, ComponentBase):
            raise ValueError("Child external is not a component.")
        if not model.hasVA(extnl, "pixelSize"):
            raise ValueError("Child external is not a Emitter component.")
        self._external = extnl
        self.children.value.add(extnl)

        intnl = children["internal"]
        if not isinstance(intnl, ComponentBase):
            raise ValueError("Child internal is not a component.")
        if not model.hasVA(intnl, "pixelSize"):
            raise ValueError("Child internal is not a Emitter component.")
        self._internal = intnl
        self.children.value.add(intnl)

        # Copy VAs from external
        if model.hasVA(self._external, "pixelSize"):
            self.pixelSize = self._external.pixelSize
        if model.hasVA(self._external, "translation"):
            self.translation = self._external.translation
        if model.hasVA(self._external, "resolution"):
            self.resolution = self._external.resolution
        if model.hasVA(self._external, "scale"):
            self.scale = self._external.scale
        if model.hasVA(self._external, "rotation"):
            self.rotation = self._external.rotation
        if model.hasVA(self._external, "dwellTime"):
            self.dwellTime = self._external.dwellTime
        self._shape = self._external.shape

        # Copy VAs from internal
        if model.hasVA(self._internal, "horizontalFoV"):
            self.horizontalFoV = self._internal.horizontalFoV
            # Create read-only magnification VA
            mag = self._external.HFWNoMag / self.horizontalFoV.value
            self.magnification = model.VigilantAttribute(mag, unit="", readonly=True)
            self.horizontalFoV.subscribe(self._updateMagnification, init=True)
        elif model.hasVA(self._external, "magnification"):
            self.magnification = self._external.magnification
        if model.hasVA(self._internal, "accelVoltage"):
            self.accelVoltage = self._internal.accelVoltage
        if model.hasVA(self._internal, "power"):
            self.power = self._internal.power
        if model.hasVA(self._internal, "probeCurrent"):
            self.probeCurrent = self._internal.probeCurrent

    def _updateMagnification(self, hfw):
        new_mag = self._external.HFWNoMag / hfw
        self.magnification._value = new_mag
        self.magnification.notify(new_mag)
        # Also update external magnification
        self._external.magnification.value = new_mag
