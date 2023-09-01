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
import logging
from typing import Hashable

from odemis import model
from odemis.model import ComponentBase


class CompositedScanner(model.Emitter):
    '''
    A generic Emitter which takes 2 dependencies to create a scanner. It's
    essentially a wrapper to an Emitter to generate data using the "external"
    scanner while manipulating HFW, accelerating voltage and probe current via
    the "internal" scanner.
    '''

    def __init__(self, name, role, children=None, dependencies=None, daemon=None, **kwargs):
        '''
        dependencies (dict string->model.HwComponent): the dependencies
            There must be exactly two dependencies "external" and "internal".
        children (dict str->dict): An optional "detector" which will create a
            CompositedDetector
        Raise:
          ValueError: if the dependencies are not compatible
        '''
        if dependencies is None:
            dependencies = {}

        if children is None:
            children = {}

        # we will fill the set of dependencies with Components later in ._dependencies
        model.Emitter.__init__(self, name, role, dependencies=dependencies, daemon=daemon, **kwargs)

        # Check the dependencies
        extnl = dependencies["external"]
        if not isinstance(extnl, ComponentBase):
            raise ValueError("Dependency external is not a component.")
        if not model.hasVA(extnl, "pixelSize"):
            raise ValueError("Dependency external is not a Emitter component.")
        self._external_scanner = extnl

        intnl = dependencies["internal"]
        if not isinstance(intnl, ComponentBase):
            raise ValueError("Dependency internal is not a component.")
        if not hasattr(intnl, "shape"):
            # Note: the internal component doesn't need to provide pixelSize
            raise ValueError("Dependency internal is not a Emitter component.")
        self._internal_scanner = intnl

        # Copy VAs directly related to scanning from external
        self._shape = self._external_scanner.shape
        for vaname in ("pixelSize", "translation", "resolution", "scale",
                       "dwellTime"):
            if model.hasVA(self._external_scanner, vaname):
                va = getattr(self._external_scanner, vaname)
                setattr(self, vaname, va)

        # Copy VAs for controlling the ebeam from internal
        # horizontalFoV or magnification need a bit more cleverness
        if model.hasVA(self._internal_scanner, "horizontalFoV"):
            self.horizontalFoV = self._internal_scanner.horizontalFoV
            # Create read-only magnification VA
            # TODO: why not just using the magnification VA from the internal?
            self.magnification = model.VigilantAttribute(1, unit="", readonly=True)
            self.horizontalFoV.subscribe(self._updateMagnification, init=True)
        elif model.hasVA(self._external_scanner, "magnification"):
            self.magnification = self._external_scanner.magnification

        # TODO: just pick every VAs which are not yet on self?
        for vaname in ("accelVoltage", "probeCurrent", "depthOfField", "spotSize", "shift"):
            if model.hasVA(self._internal_scanner, vaname):
                va = getattr(self._internal_scanner, vaname)
                setattr(self, vaname, va)

        # VAs that could be both on internal or external. If on both, pick internal
        # TODO: add a better way to select if both provide: either via arg, or
        # select the one which provides a None (=auto), or which is not read-only?
        va_names = ["power", "rotation"]

        # Components to use to control the VAs
        self._va_external_ctrl = None
        self._va_blanker_ctrl = None

        for cname, ckwargs in children.items():
            if not cname.startswith("detector"):
                raise ValueError(f"Only supports children with role as 'detector...', but got '{cname}'")

            detector = CompositedDetector(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(detector)

            # Setting up the automatic blanker on the composited scanner
            if model.hasVA(self._external_scanner, "blanker") and None in self._external_scanner.blanker.choices:
                self.blanker = self._external_scanner.blanker
            elif model.hasVA(self._external_scanner, "blanker") and None not in self._external_scanner.blanker.choices:
                self._createAutoBlanker(self._external_scanner)
            elif model.hasVA(self._internal_scanner, "blanker"):  # Internal blankers are never automatic, so we create one
                self._createAutoBlanker(self._internal_scanner)
            else:
                logging.debug("No blanker supported for the Composited Scanner.")

            # Setting up the automatic external VA on the composited scanner (For switching between acquisition mode and external mode on the SEM)
            if model.hasVA(self._external_scanner, "external") and None in self._external_scanner.external.choices:
                self.external = self._external_scanner.external
            elif model.hasVA(self._external_scanner, "external") and None not in self._external_scanner.external.choices:
                self._createAutoExternal(self._external_scanner)
            elif model.hasVA(self._internal_scanner, "external"):
                self._createAutoExternal(self._internal_scanner)
            else:
                logging.debug("No external VA supported for the Composited Scanner.")

        # If no automatic blanker/external, just duplicate the original ones
        if not self.children.value:
            va_names += ["blanker", "external"]

        for vaname in va_names:
            if model.hasVA(self._internal_scanner, vaname):
                va = getattr(self._internal_scanner, vaname)
                setattr(self, vaname, va)
            elif model.hasVA(self._external_scanner, vaname):
                va = getattr(self._external_scanner, vaname)
                setattr(self, vaname, va)

        self._beamUsers = set()  # contains any object

    def _createAutoBlanker(self, blanker_ctrl):
        """
        Creates a .blanker VA with an option None to request it's automatically
          disabled when acquiring with the DataFlow.
        blanker_ctrl (Scanner): component with a blanker VA to set the actual
          blanker state on/off
        """
        if self._va_blanker_ctrl is not None:
            return

        self._va_blanker_ctrl = blanker_ctrl
        self.blanker = model.VAEnumerated(
            None,
            setter=self._setBlanker,
            choices={True: 'blanked', False: 'unblanked', None: 'auto'})

    def _createAutoExternal(self, external_ctrl):
        """
        Creates a .external VA with an option None to request it's automatically
          enabled when acquiring with the DataFlow.
        external_ctrl (Scanner): component with a external VA to set the actual
          blanker state on/off
        """
        if self._va_external_ctrl is not None:
            return

        self._va_external_ctrl = external_ctrl
        self.external = model.VAEnumerated(
            None,
            setter=self._setExternal,
            choices={True: 'external', False: 'internal', None: 'auto'})

    def _setBlanker(self, blank):
        if blank is not None:
            self._va_blanker_ctrl.blanker.value = blank
        else:
            self._va_blanker_ctrl.blanker.value = not bool(self._beamUsers)

        return blank

    def _setExternal(self, mode):
        if mode is not None:
            self._va_external_ctrl.external.value = mode
        else:
            self._va_external_ctrl.external.value = bool(self._beamUsers)

        return mode

    def _updateMagnification(self, hfw):
        new_mag = self._external_scanner.HFWNoMag / hfw
        self.magnification._value = new_mag
        self.magnification.notify(new_mag)
        # Also update external magnification
        self._external_scanner.magnification.value = new_mag

    # Share the metadata with the external, which is the one that will actually
    # generate the data (with the metadata)
    # TODO: merge the metadata from the internal
    def updateMetadata(self, md):
        self._external_scanner.updateMetadata(md)

    def getMetadata(self):
        return self._external_scanner.getMetadata()

    def claimBeam(self, claim: bool, user: Hashable):
        """
        Used to indicate the start and end of beam usage. It takes care of
          updating the blanker and external control, if present and set to None
          (ie, automatic control).
        :param claim (boolean): True for when starting to use the beam. False when
          not using the beam any more.
        :param user: an object used to identify who is claiming (or not) the beam
        """
        if claim:
            self._beamUsers.add(user)
        else:
            self._beamUsers.discard(user)

        in_use = bool(self._beamUsers)

        if self._va_blanker_ctrl and self.blanker.value is None:
            # Disable the blanker when using the beam
            self._va_blanker_ctrl.blanker.value = not in_use

        if self._va_external_ctrl and self.external.value is None:
            # Activate the external mode when using the beam
            self._va_external_ctrl.external.value = in_use


class CompositedDetector(model.Detector):
    '''
    A wrapper Detector which can be used in addition to the CompositedScanner.
    It's used to automatically control the external and/or blanker when an
    acquisition starts and stops.
    '''
    def __init__(self, name, role, parent, dependencies, **kwargs):
        '''
        parent (CompositedScanner): CompositedScanner class  for use on the dataflow.
        dependencies (dict string->model.HwComponent): the dependencies
            There must be one dependency "external", a Detector.
        Raise:
          ValueError: if the dependencies are not compatible
        '''
        super().__init__(name, role, parent=parent, dependencies=dependencies, **kwargs)

        # Check the dependencies
        extnl = dependencies["external"]
        if not isinstance(extnl, ComponentBase) and not hasattr(extnl, "data") and not hasattr(extnl, "shape"):
            raise ValueError("Dependency external is not a Detector component.")
        self._external_det = extnl

        # Special event to request software unblocking on the scan
        if hasattr(self._external_det, "softwareTrigger") and isinstance(self._external_det.softwareTrigger, model.EventBase):
            self.softwareTrigger = self._external_det.softwareTrigger

        self._shape = self._external_det.shape
        self.data = CompositedDataflow(self._external_det, self.parent)

    # Share the metadata with the external, which is the one that will actually
    # generate the data (with the metadata)
    # TODO: merge the metadata from the internal
    def updateMetadata(self, md):
        self._external_det.updateMetadata(md)

    def getMetadata(self):
        return self._external_det.getMetadata()


class CompositedDataflow(model.DataFlow):
    def __init__(self, external_detector, composited_scanner):
        """
        Combines an external dataflow and controls the external and blanker VA's using the method claimBeam.

        :param external_detector (Detector): The external detector that the dataflow corresponds to
        :param composited_scanner (CompositedScanner): The SEM which can be used to update the scan mode
        """
        super().__init__()
        self._external_dataflow = external_detector.data
        self._composited_scanner = composited_scanner

    def start_generate(self):
        """
        Sets the scan mode to "external" and subscribes self.notify to the external dataflow
        :return:
        """
        self._composited_scanner.claimBeam(True, self)
        self._external_dataflow.subscribe(self._on_data)

    def stop_generate(self):
        """
        Unsubscribes self.notify of the external dataflow and updates the external and blanker VA's
        """
        self._external_dataflow.unsubscribe(self._on_data)
        self._composited_scanner.claimBeam(False, self)

    def _on_data(self, dataflow, data):
        """
        Wrapper to only pass data to the notify of the base class.
        """
        super().notify(data)
