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

        # list of 2-tuples (function, *arg): to be called on terminate
        self._onTerminate = []
        # All the components already observed
        # str -> set of str: name of affecting component -> names of affected
        self._observed = collections.defaultdict(set)

        microscope.alive.subscribe(self._onAlive, init=True)

    def _onAlive(self, components):
        """
        Called when alive is changed => some component started or died
        """
        # For each component
        # For each component it affects
        # Subscribe to the changes of the attributes that matter
        for a in components:
            for dn in a.affects.value:
                # TODO: if component not alive yet, wait for it
                try:
                    d = model.getComponent(name=dn)
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
                    self.observeStage(a, d)
                    #TODO : support more metadata
                elif a.role == "lens":
                    # update the pixel size, mag, and pole position
                    self.observeLens(a, d)
#                elif a.role == "filter":
#                    # update the received light wavelength
#                    self.observeFilter(a, d)
                elif a.role == "light":
                    # update the emitted light wavelength
                    self.observeLight(a, d)
                else:
                    logging.debug("not observing %s which affects %s", a.name, d.name)
                    continue

                logging.info("Observing affect %s -> %s", a.name, dn)
                self._observed[a.name].add(dn)

        # TODO: drop subscriptions to dead components

    def observeStage(self, stage, comp):
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

        stage.position.subscribe(updateStagePos)
        updateStagePos(stage.position.value)
        self._onTerminate.append((stage.position.unsubscribe, (updateStagePos,)))

    def observeLens(self, lens, comp):
        if comp.role != "ccd":
            logging.warning("Does not know what to do with a lens in front of a %s", comp.role)
            return

        # Depends on the actual size of the ccd's density (should be constant)
        captor_mpp = comp.pixelSize.value # m, m

        # update static information
        md = {model.MD_LENS_NAME: lens.hwVersion}
        comp.updateMetadata(md)

        # we need to keep the information on the detector to update
        def updatePixelDensity(unused, lens=lens, comp=comp):
            # the formula is very simple: actual MpP = CCD MpP * binning / Mag
            try:
                binning = comp.binning.value
            except AttributeError:
                binning = 1, 1
            mag = lens.magnification.value
            mpp = (captor_mpp[0] * binning[0] / mag, captor_mpp[1] * binning[1] / mag) 
            md = {model.MD_PIXEL_SIZE: mpp,
                  model.MD_LENS_MAG: mag}
            comp.updateMetadata(md)

        lens.magnification.subscribe(updatePixelDensity)
        self._onTerminate.append((lens.magnification.unsubscribe, (updatePixelDensity,)))
        try:
            comp.binning.subscribe(updatePixelDensity)
            self._onTerminate.append((comp.binning.unsubscribe, (updatePixelDensity,)))
        except AttributeError:
            pass
        updatePixelDensity(None) # update it right now

        # update pole position, if available
        if (hasattr(lens, "polePosition")
            and isinstance(lens.polePosition, model.VigilantAttributeBase)):
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

            lens.polePosition.subscribe(updatePolePos)
            self._onTerminate.append((lens.polePosition.unsubscribe, (updatePolePos,)))
            try:
                comp.binning.subscribe(updatePolePos)
                self._onTerminate.append((comp.binning.unsubscribe, (updatePolePos,)))
            except AttributeError:
                pass
            updatePolePos(None) # update it right now

        # update AR data, if available
        md_va_list = [("xMax", model.MD_AR_XMAX),
                      ("holeDiameter", model.MD_AR_HOLE_DIAMETER),
                      ("focusDistance", model.MD_AR_FOCUS_DISTANCE),
                      ("parabolaF", model.MD_AR_PARABOLA_F)]
        for va_name, md_key in md_va_list:
            if (hasattr(lens, va_name)
                and isinstance(getattr(lens, va_name), model.VigilantAttributeBase)):
                va = getattr(lens, va_name)
                def updateARData(unused, va=va, comp=comp):
                    md = {md_key: va.value}
                    comp.updateMetadata(md)

                va.subscribe(updateARData)
                self._onTerminate.append((va.unsubscribe, (updateARData,)))
                updateARData(None)  # update it right now

    def observeLight(self, light, comp):

        def updateInputWL(emissions, light=light, comp=comp):
            # indexes of the wavelengths activated
            em_on = [i for i, e in enumerate(emissions) if e > 0]
            if not em_on: # No light
                wl = (0, 0)
            elif len(em_on) == 1: # just one light
                wl = light.spectra.value[em_on[0]]
            else: # multiple wavelengths
                # compute the min/max from the emissions which are not 0
                miniwl = 1 # 1m is huge
                maxiwl = 0

                for i, e in enumerate(emissions):
                    if e > 0:
                        miniwl = min(miniwl, light.spectra.value[i][2])
                        maxiwl = max(maxiwl, light.spectra.value[i][4])
                wl = (miniwl, maxiwl)

            md = {model.MD_IN_WL: wl}
            comp.updateMetadata(md)

        def updateLightPower(power, light=light, comp=comp):
            p = power * numpy.sum(light.emissions.value)
            md = {model.MD_LIGHT_POWER: p}
            comp.updateMetadata(md)

        light.power.subscribe(updateLightPower, init=True)
        self._onTerminate.append((light.power.unsubscribe, (updateLightPower,)))

        light.emissions.subscribe(updateInputWL, init=True)
        self._onTerminate.append((light.emissions.unsubscribe, (updateInputWL,)))

    def terminate(self):
        # call all the unsubscribes
        for fun, args in self._onTerminate:
            try:
                fun(*args)
            except:
                logging.exception("Failed to unsubscribe metadata properly.")

        model.Component.terminate(self)
