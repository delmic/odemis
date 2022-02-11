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
                elif a.role == "pol-analyzer":
                    # update the position of the polarization analyzer
                    observed = self.observePolAnalyzer(a, d)
                elif a.role == "streak-lens":
                    # update the magnification of the streak lens
                    observed = self.observeStreakLens(a, d)
                elif a.role == "e-beam":
                    observed = self.observeEbeam(a, d)
                else:
                    observed = False

                if observed:
                    logging.info("Observing affect %s -> %s", a.name, dn)
                else:
                    logging.info("Not observing unhandled affect %s (%s) -> %s (%s)",
                                 a.name, a.role, dn, d.role)

                self._observed[a.name].add(dn)

        # TODO: drop subscriptions to dead components

# Note: The scope of variables is redefined in the nested/local function
# to ensure that the correct variables are used and were not overwritten
# before calling the function. That's why all the local functions are written
# with extra arguments such as "a=a" (for more info on this issue see:
# https://eev.ee/blog/2011/04/24/gotcha-python-scoping-closures/)

    def observeStage(self, stage, comp_affected):
        """
        return bool: True if will actually update the affected component,
                     False if the affect is not supported (here)
        """

        # we need to keep the information on the detector to update
        def updateStagePos(pos, comp_affected=comp_affected):
            # We need axes X and Y
            if "x" not in pos or "y" not in pos:
                logging.warning("Stage position doesn't contain X/Y axes")
            # if unknown, just assume a fixed position
            x = pos.get("x", 0)
            y = pos.get("y", 0)
            md = {model.MD_POS: (x, y)}
            logging.debug("Updating position for component %s, to %f, %f",
                          comp_affected.name, x, y)
            comp_affected.updateMetadata(md)

        stage.position.subscribe(updateStagePos, init=True)
        self._onTerminate.append((stage.position.unsubscribe, (updateStagePos,)))

        return True

    def observeLens(self, lens, comp_affected):
        # Only update components with roles of ccd*, sp-ccd*, or laser-mirror*
        if not any(comp_affected.role.startswith(r) for r in ("ccd", "sp-ccd", "laser-mirror", "diagnostic-ccd")):
            return False

        # update static information
        md = {model.MD_LENS_NAME: lens.hwVersion}
        comp_affected.updateMetadata(md)

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
        if model.hasVA(comp_affected, "scale"):
            md_va_list["magnification"] = model.MD_LENS_MAG
        else:
            # TODO: instead of updating PIXEL_SIZE everytime the CCD changes binning,
            # just let the CCD component compute the value based on its sensor
            # pixel size + MAG, like for the scanners.
            if model.hasVA(comp_affected, "binning"):
                binva = comp_affected.binning
            else:
                logging.debug("No binning")
                binva = None

            # Depends on the actual size of the ccd's density (should be constant)
            captor_mpp = comp_affected.pixelSize.value  # m, m

            # we need to keep the information on the detector to update
            def updatePixelDensity(unused, lens=lens, comp_affected=comp_affected, binva=binva):
                # unused: because it might be magnification or binning

                # the formula is very simple: actual MpP = CCD MpP * binning / Mag
                if binva is None:
                    binning = 1, 1
                else:
                    binning = binva.value
                mag = lens.magnification.value
                mpp = (captor_mpp[0] * binning[0] / mag, captor_mpp[1] * binning[1] / mag)
                md = {model.MD_PIXEL_SIZE: mpp,
                      model.MD_LENS_MAG: mag}
                comp_affected.updateMetadata(md)

            lens.magnification.subscribe(updatePixelDensity, init=True)
            self._onTerminate.append((lens.magnification.unsubscribe, (updatePixelDensity,)))
            binva.subscribe(updatePixelDensity)
            self._onTerminate.append((binva.unsubscribe, (updatePixelDensity,)))

        # update pole position (if available), taking into account the binning
        if model.hasVA(lens, "polePosition"):
            def updatePolePos(unused, lens=lens, comp_affected=comp_affected):
                # unused: because it might be polePos or binning

                # the formula is: Pole = Pole_no_binning / binning
                try:
                    binning = comp_affected.binning.value
                except AttributeError:
                    binning = 1, 1
                pole_pos = lens.polePosition.value
                pp = (pole_pos[0] / binning[0], pole_pos[1] / binning[1])
                md = {model.MD_AR_POLE: pp}
                comp_affected.updateMetadata(md)

            lens.polePosition.subscribe(updatePolePos, init=True)
            self._onTerminate.append((lens.polePosition.unsubscribe, (updatePolePos,)))
            try:
                comp_affected.binning.subscribe(updatePolePos)
                self._onTerminate.append((comp_affected.binning.unsubscribe, (updatePolePos,)))
            except AttributeError:
                pass

        # update metadata for VAs which can be directly copied
        for va_name, md_key in md_va_list.items():
            if model.hasVA(lens, va_name):

                def updateMDFromVA(val, md_key=md_key, comp_affected=comp_affected):
                    md = {md_key: val}
                    comp_affected.updateMetadata(md)

                logging.debug("Listening to VA %s.%s -> MD %s", lens.name, va_name, md_key)
                va = getattr(lens, va_name)
                va.subscribe(updateMDFromVA, init=True)
                self._onTerminate.append((va.unsubscribe, (updateMDFromVA,)))

        return True

    def observeLight(self, light, comp_affected):
        def updateLightPower(power, light=light, comp_affected=comp_affected):
            # MD_IN_WL expects just min/max => if multiple sources, we need to combine
            spectra = light.spectra.value
            wls = []
            for i, intens in enumerate(power):
                if intens > 0:
                    wls.append((spectra[i][0], spectra[i][-1]))

            if wls:
                wl_range = (min(w[0] for w in wls),
                            max(w[1] for w in wls))
            else:
                wl_range = (0, 0)

            md = {model.MD_IN_WL: wl_range, model.MD_LIGHT_POWER: sum(power)}
            comp_affected.updateMetadata(md)

        light.power.subscribe(updateLightPower, init=True)
        self._onTerminate.append((light.power.unsubscribe, (updateLightPower,)))

        return True

    def observeSpectrograph(self, spectrograph, comp_affected):
        if comp_affected.role != "monochromator":
            return False

        if 'slit-monochromator' not in spectrograph.axes:
            logging.info("No 'slit-monochromator' axis was found, will not be able to compute monochromator bandwidth.")

            def updateOutWLRange(pos, comp_affected=comp_affected):
                wl = pos["wavelength"]
                md = {model.MD_OUT_WL: (wl, wl)}
                comp_affected.updateMetadata(md)

        else:
            def updateOutWLRange(pos, sp=spectrograph, comp_affected=comp_affected):
                width = pos['slit-monochromator']
                bandwidth = sp.getOpeningToWavelength(width)
                md = {model.MD_OUT_WL: bandwidth}
                comp_affected.updateMetadata(md)

        spectrograph.position.subscribe(updateOutWLRange, init=True)
        self._onTerminate.append((spectrograph.position.unsubscribe, (updateOutWLRange,)))

        return True

    def observeFilter(self, filter, comp_affected):
        # FIXME: If a monochromator + spectrograph, which MD_OUT_WL to pick?
        # update any affected component
        def updateOutWLRange(pos, fl=filter, comp_affected=comp_affected):
            wl_out = fl.axes["band"].choices[pos["band"]]
            comp_affected.updateMetadata({model.MD_OUT_WL: wl_out})

        filter.position.subscribe(updateOutWLRange, init=True)
        self._onTerminate.append((filter.position.unsubscribe, (updateOutWLRange,)))

        return True

    def observeQWP(self, qwp, comp_affected):

        if model.hasVA(qwp, "position"):
            def updatePosition(pos, comp_affected=comp_affected):
                md = {model.MD_POL_POS_QWP: pos["rz"]}
                comp_affected.updateMetadata(md)

            qwp.position.subscribe(updatePosition, init=True)
            self._onTerminate.append((qwp.position.unsubscribe, (updatePosition,)))

        return True

    def observeLinPol(self, linpol, comp_affected):

        if model.hasVA(linpol, "position"):
            def updatePosition(pos, comp_affected=comp_affected):
                md = {model.MD_POL_POS_LINPOL: pos["rz"]}
                comp_affected.updateMetadata(md)

            linpol.position.subscribe(updatePosition, init=True)
            self._onTerminate.append((linpol.position.unsubscribe, (updatePosition,)))

        return True

    def observePolAnalyzer(self, analyzer, comp_affected):

        if model.hasVA(analyzer, "position"):
            def updatePosition(pos, comp_affected=comp_affected):
                md = {model.MD_POL_MODE: pos["pol"]}
                comp_affected.updateMetadata(md)

            analyzer.position.subscribe(updatePosition, init=True)
            self._onTerminate.append((analyzer.position.unsubscribe, (updatePosition,)))

        return True

    def observeStreakLens(self, streak_lens, comp_affected):
        """Update the magnification of the streak lens affecting the
        streak readout camera."""

        if not comp_affected.role.endswith("ccd"):
            return False

        def updateMagnification(mag, comp_affected=comp_affected):
            md = {model.MD_LENS_MAG: mag}
            comp_affected.updateMetadata(md)

        streak_lens.magnification.subscribe(updateMagnification, init=True)
        self._onTerminate.append((streak_lens.magnification.unsubscribe, (updateMagnification,)))

        return True

    def observeEbeam(self, ebeam, comp_affected):
        """Add ebeam rotation to multibeam metadata to make sure that the thumbnails
        are displayed correctly."""

        if comp_affected.role != "multibeam":
            return False

        def updateRotation(rot, comp_affected=comp_affected):
            md = {model.MD_ROTATION: rot}
            comp_affected.updateMetadata(md)

        ebeam.rotation.subscribe(updateRotation, init=True)
        self._onTerminate.append((ebeam.rotation.unsubscribe, (updateRotation,)))

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
