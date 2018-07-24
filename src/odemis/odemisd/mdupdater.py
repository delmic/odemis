# -*- coding: utf-8 -*-
'''
Created on 20 Aug 2012

@author: Éric Piel

Copyright © 2012-2014 Éric Piel, Delmic

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

import collections
import gc
import logging
import numpy
from odemis import model


class MetadataUpdater(model.Component):
    '''
    Takes care of updating the metadata of detectors, based on the physical
    attributes of other components in the system.
    This implementation is specific to microscopes.
    '''
    # This is kept in a separate module from the main backend because it has to
    # know the business semantic.

    def __init__(self, name, microscope, **kwargs):
        '''
        microscope (model.Microscope): the microscope to observe and update
        '''
        model.Component.__init__(self, name, **kwargs)

        # Warning: for efficiency, we want to run in the same container as the back-end
        # but this means the back-end is not running yet when we are created
        # so we cannot access the back-end.
        self._mic = microscope

        # keep list of already accessed components, to avoid creating new proxys
        # every time the mode changes
        self._known_comps = dict()  # str (name) -> component

        # list of 2-tuples (function, *arg): to be called on terminate
        self._onTerminate = []
        # All the components already observed
        # str -> set of str: name of affecting component -> names of affected
        self._observed = collections.defaultdict(set)

        microscope.alive.subscribe(self._onAlive, init=True)

    def _getComponent(self, name):
        """
        same as model.getComponent, but optimised by caching the result
        return Component
        raise LookupError: if no component found
        """
        try:
            comp = self._known_comps[name]
        except LookupError:
            comp = model.getComponent(name=name)
            self._known_comps[name] = comp

        return comp

    def _onAlive(self, components):
        """
        Called when alive is changed => some component started or died
        """
        # For each component
        # For each component it affects
        # Subscribe to the changes of the attributes that matter
        for a in components:  # component in components of microscope
            for dn in a.affects.value:
                # TODO: if component not alive yet, wait for it
                try:
                    d = self._getComponent(dn)  # get components affected when changing the value of a
                except LookupError:
                    # TODO: stop subscriptions if the component was there (=> just died)
                    self._observed[a.name].discard(dn)
                    continue
                else:
                    if dn in self._observed[a.name]:
                        # already subscribed
                        continue

                if a.role == "stage":
                    # update the image position
                    observed = self.observeStage(a, d)
                elif a.role == "lens":
                    # update the pixel size, mag, and pole position
                    observed = self.observeLens(a, d)
                elif a.role == "light":
                    # update the emitted light wavelength
                    observed = self.observeLight(a, d)
                elif a.role and a.role.startswith("spectrograph"):  # spectrograph-XXX too
                    # update the output wavelength range
                    observed = self.observeSpectrograph(a, d)
                elif a.role in ("cl-filter", "filter"):
                    # update the output wavelength range
                    observed = self.observeFilter(a, d)
                elif a.role == "quarter-wave-plate":
                    # update the position of the qwp in the polarization analyzer
                    observed = self.observeQWP(a, d)
                elif a.role == "lin-pol":
                    # update the position of the linear polarizer in the polarization analyzer
                    observed = self.observeLinPol(a, d)
                else:
                    observed = False

                if observed:
                    logging.info("Observing affect %s -> %s", a.name, dn)
                else:
                    logging.info("Not observing unhandled affect %s (%s) -> %s (%s)",
                                 a.name, a.role, dn, d.role)

                self._observed[a.name].add(dn)

        # TODO: drop subscriptions to dead components

    def observeStage(self, stage, comp):
        """
        return bool: True if will actually update the affected component,
                     False if the affect is not supported (here)
        """

        # we need to keep the information on the detector to update
        def updateStagePos(pos, comp=comp):
            # We need axes X and Y
            if "x" not in pos or "y" not in pos:
                logging.warning("Stage position doesn't contain X/Y axes")
            # if unknown, just assume a fixed position
            x = pos.get("x", 0)
            y = pos.get("y", 0)
            md = {model.MD_POS: (x, y)}
            logging.debug("Updating position for component %s, to %f, %f",
                          comp.name, x, y)
            comp.updateMetadata(md)

        stage.position.subscribe(updateStagePos, init=True)
        self._onTerminate.append((stage.position.unsubscribe, (updateStagePos,)))

        return True

    def observeLens(self, lens, comp):
        if comp.role not in ("ccd", "sp-ccd", "laser-mirror"):
            return False

        # update static information
        md = {model.MD_LENS_NAME: lens.hwVersion}
        comp.updateMetadata(md)

        # List of direct VA -> MD mapping
        md_va_list = {"numericalAperture": model.MD_LENS_NA,
                      "refractiveIndex": model.MD_LENS_RI,
                      "xMax": model.MD_AR_XMAX,
                      "holeDiameter": model.MD_AR_HOLE_DIAMETER,
                      "focusDistance": model.MD_AR_FOCUS_DISTANCE,
                      "parabolaF": model.MD_AR_PARABOLA_F,
                      "rotation": model.MD_ROTATION,
                     }

        # If it's a scanner (ie, it has a "scale"), the component will take care
        # by itself of updating .pixelSize and MD_PIXEL_SIZE depending on the
        # MD_LENS_MAG.
        # For DigitalCamera, the .pixelSize is the SENSOR_PIXEL_SIZE, and we
        # compute PIXEL_SIZE every time the LENS_MAG *or* binning change.
        if model.hasVA(comp, "scale"):
            md_va_list["magnification"] = model.MD_LENS_MAG
        else:
            # TODO: instead of updating PIXEL_SIZE everytime the CCD changes binning,
            # just let the CCD component compute the value based on its sensor
            # pixel size + MAG, like for the scanners.
            if model.hasVA(comp, "binning"):
                binva = comp.binning
            else:
                logging.debug("No binning")
                binva = None

            # Depends on the actual size of the ccd's density (should be constant)
            captor_mpp = comp.pixelSize.value  # m, m

            # we need to keep the information on the detector to update
            def updatePixelDensity(unused, lens=lens, comp=comp, binva=binva):
                # the formula is very simple: actual MpP = CCD MpP * binning / Mag
                if binva is None:
                    binning = 1, 1
                else:
                    binning = binva.value
                mag = lens.magnification.value
                mpp = (captor_mpp[0] * binning[0] / mag, captor_mpp[1] * binning[1] / mag)
                md = {model.MD_PIXEL_SIZE: mpp,
                      model.MD_LENS_MAG: mag}
                comp.updateMetadata(md)

            lens.magnification.subscribe(updatePixelDensity, init=True)
            self._onTerminate.append((lens.magnification.unsubscribe, (updatePixelDensity,)))
            binva.subscribe(updatePixelDensity)
            self._onTerminate.append((binva.unsubscribe, (updatePixelDensity,)))

        # update pole position (if available), taking into account the binning
        if model.hasVA(lens, "polePosition"):
            def updatePolePos(unused, lens=lens, comp=comp):
                # the formula is: Pole = Pole_no_binning / binning
                try:
                    binning = comp.binning.value
                except AttributeError:
                    binning = 1, 1
                pole_pos = lens.polePosition.value
                pp = (pole_pos[0] / binning[0], pole_pos[1] / binning[1])
                md = {model.MD_AR_POLE: pp}
                comp.updateMetadata(md)

            lens.polePosition.subscribe(updatePolePos, init=True)
            self._onTerminate.append((lens.polePosition.unsubscribe, (updatePolePos,)))
            try:
                comp.binning.subscribe(updatePolePos)
                self._onTerminate.append((comp.binning.unsubscribe, (updatePolePos,)))
            except AttributeError:
                pass

        # update metadata for VAs which can be directly copied
        for va_name, md_key in md_va_list.items():
            if model.hasVA(lens, va_name):

                def updateMDFromVA(val, md_key=md_key, comp=comp):
                    md = {md_key: val}
                    comp.updateMetadata(md)

                logging.debug("Listening to VA %s.%s -> MD %s", lens.name, va_name, md_key)
                va = getattr(lens, va_name)
                va.subscribe(updateMDFromVA, init=True)
                self._onTerminate.append((va.unsubscribe, (updateMDFromVA,)))

        return True

    def observeLight(self, light, comp):

        def updateInputWL(emissions, light=light, comp=comp):
            # MD_IN_WL expects just min/max => if multiple sources, we need to combine
            spectra = light.spectra.value
            wls = []
            for i, intens in enumerate(emissions):
                if intens > 0:
                    wls.append((spectra[i][0], spectra[i][-1]))

            if wls:
                wl_range = (min(w[0] for w in wls),
                            max(w[1] for w in wls))
            else:
                wl_range = (0, 0)

            # FIXME: not sure how to combine
            power = light.power.value
            p = power * numpy.sum(emissions)

            md = {model.MD_IN_WL: wl_range,
                  model.MD_LIGHT_POWER: p}
            comp.updateMetadata(md)

        def updateLightPower(power, light=light, comp=comp):
            p = power * numpy.sum(light.emissions.value)
            md = {model.MD_LIGHT_POWER: p}
            comp.updateMetadata(md)

        light.power.subscribe(updateLightPower, init=True)
        self._onTerminate.append((light.power.unsubscribe, (updateLightPower,)))

        light.emissions.subscribe(updateInputWL, init=True)
        self._onTerminate.append((light.emissions.unsubscribe, (updateInputWL,)))

        return True

    def observeSpectrograph(self, spectrograph, comp):
        if comp.role != "monochromator":
            return False

        if 'slit-monochromator' not in spectrograph.axes:
            logging.info("No 'slit-monochromator' axis was found, will not be able to compute monochromator bandwidth.")

            def updateOutWLRange(pos, sp=spectrograph, comp=comp):
                wl = sp.position.value["wavelength"]
                md = {model.MD_OUT_WL: (wl, wl)}
                comp.updateMetadata(md)

        else:
            def updateOutWLRange(pos, sp=spectrograph, comp=comp):
                width = pos['slit-monochromator']
                bandwidth = sp.getOpeningToWavelength(width)
                md = {model.MD_OUT_WL: bandwidth}
                comp.updateMetadata(md)

        spectrograph.position.subscribe(updateOutWLRange, init=True)
        self._onTerminate.append((spectrograph.position.unsubscribe, (updateOutWLRange,)))

        return True

    def observeFilter(self, filter, comp):
        # FIXME: If a monochromator + spectrograph, which MD_OUT_WL to pick?
        # update any affected component
        def updateOutWLRange(pos, fl=filter, comp=comp):
            wl_out = fl.axes["band"].choices[fl.position.value["band"]]
            comp.updateMetadata({model.MD_OUT_WL: wl_out})

        filter.position.subscribe(updateOutWLRange, init=True)
        self._onTerminate.append((filter.position.unsubscribe, (updateOutWLRange,)))

        return True

    def observeQWP(self, qwp, comp_affected):

        if model.hasVA(qwp, "position"):
            def updatePosition(unused, qwp=qwp, comp_affected=comp_affected):
                pos = qwp.position.value["rz"]
                md = {model.MD_POL_POS_QWP: pos}
                comp_affected.updateMetadata(md)

            qwp.position.subscribe(updatePosition, init=True)
            self._onTerminate.append((qwp.position.unsubscribe, (updatePosition,)))

        return True

    def observeLinPol(self, linpol, comp_affected):

        if model.hasVA(linpol, "position"):
            def updatePosition(unused, linpol=linpol, comp_affected=comp_affected):
                pos = linpol.position.value["rz"]
                md = {model.MD_POL_POS_LINPOL: pos}
                comp_affected.updateMetadata(md)

            linpol.position.subscribe(updatePosition, init=True)
            self._onTerminate.append((linpol.position.unsubscribe, (updatePosition,)))

        return True

    def terminate(self):
        self._mic.alive.unsubscribe(self._onAlive)

        # call all the unsubscribes
        for fun, args in self._onTerminate:
            try:
                fun(*args)
            except Exception as ex:
                logging.warning("Failed to unsubscribe metadata properly: %s", ex)

        model.Component.terminate(self)
