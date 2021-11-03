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

        if model.hasVA(self._internal, "beamShift"):
            va = getattr(self._internal, "beamShift")
            setattr(self, "beamShift", va)

        # TODO: just pick every VAs which are not yet on self?
        for vaname in ("accelVoltage", "probeCurrent", "depthOfField", "spotSize"):
            if model.hasVA(self._internal, vaname):
                va = getattr(self._internal, vaname)
                setattr(self, vaname, va)

        # VAs that could be both on internal or external. If on both, pick internal
        # TODO: add a better way to select if both provide: either via arg, or
        # select the one which provides a None (=auto), or which is not read-only?
        for vaname in ("power", "external", "rotation"):
            if model.hasVA(self._internal, vaname):
                va = getattr(self._internal, vaname)
                setattr(self, vaname, va)
            elif model.hasVA(self._external, vaname):
                va = getattr(self._external, vaname)
                setattr(self, vaname, va)



        self.blanker = model.VAEnumerated(
            None,
            setter=self._setBlanker,
            choices={True: 'blanked', False: 'unblanked', None: 'auto'})

        # TODO: if blanker has True/False (only), add a None (=auto), which
        # automatically put the underlying value based on the detector acquisition.

    def _setBlanker(self, blank):
        if not model.hasVA(self._internal, "blanker"):
            raise ValueError("Missing VA blanker on scanner")

        self._internal.blanker.value = blank

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

    def claimBeam(self, claim):
        """
        Used to claim the beam by blanking the beam on the scanner of the XT client when the blanker
        mode is set to automatic (blanker.value = None)
        :param claim (boolean): True for unblanking and False for blanking
        """
        if self.blanker.value is None:
            self._setBlanker(not claim)  # Set the blanker using the setter of the VA


class CompositedDetector(model.Detector):
    '''
    A generic Detector which takes 2 dependencies to create a one detector. It's
    essentially a wrapper to an Detector to generate data using the "external"
    detector while manipulating the "internal" detector.
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
        if not hasattr(extnl, "data") and not hasattr(extnl, "shape"):
            raise ValueError("Dependency external is not a Detector component.")
        self._external = extnl

        intnl = dependencies["internal"]
        if not isinstance(intnl, ComponentBase):
            raise ValueError("Dependency internal is not a component.")
        if not hasattr(extnl, "data") or not hasattr(extnl, "parent"):
            # Note: the internal component doesn't need to provide pixelSize
            raise ValueError("Dependency internal is not a Detector component.")
        self._internal = intnl

        comp_scanner = dependencies["comp_scanner"]
        if not isinstance(comp_scanner, ComponentBase):
            raise ValueError("Dependency internal_scanner is not a component.")
        if not hasattr(comp_scanner, "shape"):
            # Note: the internal component doesn't need to provide pixelSize
            raise ValueError("Dependency internal_scanner is not a Emitter component.")
        if not model.hasVA(comp_scanner, "external"):
            # Note: the internal component doesn't need to provide pixelSize
            raise ValueError("Dependency internal_scanner doesn't contain the necessary external VA")
        self._comp_scanner = comp_scanner

        # Special event to request software unblocking on the scan
        self.softwareTrigger = self._external.softwareTrigger
        self._shape = self._external.shape

        self.data = CompositedDataflow(self._external, self._comp_scanner)

    # Share the metadata with the external, which is the one that will actually
    # generate the data (with the metadata)
    # TODO: merge the metadata from the internal
    def updateMetadata(self, md):
        self._external.updateMetadata(md)

    def getMetadata(self):
        return self._external.getMetadata()

class CompositedDataflow(model.DataFlow):
    def __init__(self, external_detector, composited_scanner):
        """
        Combines an external dataflow and before it starts sets the correct mode on the internal sem ("external")

        :param external_dataflow (DataFlow): The external detector that the dataflow corresponds to
        :param internal_sem(xt_client.SEM): The SEM which can be used to set the scan mode to external/full_frame
        """
        model.DataFlow.__init__(self)
        self._external_dataflow = external_detector.data
        self._composited_scanner = composited_scanner

    def start_generate(self):
        """
        Sets the scan mode to "external" and subscribes self.notify to the external dataflow
        :return:
        """
        self._composited_scanner.external.value = True
        self._composited_scanner.claimBeam(True)
        self._external_dataflow.subscribe(self.notify)

    def stop_generate(self):
        """
        Unsubscribes self.notify of the external dataflow and sets the scan mode to "full_frame"
        """
        self._external_dataflow.unsubscribe(self.notify)
        self._composited_scanner.claimBeam(False)
        self._composited_scanner.external.value = False

    def notify(self, dataflow, data):
        """
        Wrapper to only pass data to the notify of the base class.
        """
        super().notify(data)