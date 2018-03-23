# -*- coding: utf-8 -*-
'''
Created on 22 Nov 2016

@author: Éric Piel

Copyright © 2016 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import logging
from odemis import model


class MultiplexLight(model.Emitter):
    """
    Light composed of multiple Lights
    """
    # There are different solutions to map power * intensities to the children.
    # In any case, the child with the max power will have the power and
    # intensities copied.
    # For the other children, the power is set with the same ratio as the
    # parent power, and the intensities are inversely proportional to the
    # max power ratio.
    # => the intensities and power are set independently

    def __init__(self, name, role, children, **kwargs):
        """
        children (dict str -> Emitter): arbitrary role -> emitter to be used as
          part of this emitter. All its provided emissions will be provided.
        """
        # TODO: allow to only use a subset of the emissions from each child

        if not children:
            raise ValueError("MultiplexLight needs children")

        model.Emitter.__init__(self, name, role, children=children, **kwargs)
        self._shape = ()

        self._child_idx = {} # Emitter -> index (shift) in the emissions/spectra

        spectra = []
        for n, child in children.items():
            if not (model.hasVA(child, "power") and
                    model.hasVA(child, "emissions") and
                    model.hasVA(child, "spectra")
                   ):
                raise ValueError("Child %s is not a light emitter" % (n,))
            self._child_idx[child] = len(spectra)
            spectra.extend(child.spectra.value)
            # TODO: update emissions whenever the child emissions change

        # Child with the maximum power range
        max_power = max(c.power.range[1] for c in self.children.value)
        self.power = model.FloatContinuous(0, (0., max_power), unit="W")
        self.power.subscribe(self._updatePower)

        # info on which source is which wavelength
        self.spectra = model.ListVA(spectra, unit="m", readonly=True)

        # It needs .spectra and .power
        pwr, em = self._readPwrEmissions()
        self.power._value = pwr

        # ratio of power per source
        # if some source don't support max power, clamped before 1
        self.emissions = model.ListVA(em, unit="", setter=self._setEmissions)

    def _updatePower(self, power):
        for child, idx in self._child_idx.items():
            cpwr = child.power.range[1] * power / self.power.range[1]
            child.power.value = child.power.clip(cpwr)
            logging.debug("Setting %s as %g W => %g W",
                          child.name, power, cpwr)

    def _readPwrEmissions(self):
        """
        Compute what should be the .power and .emissions value, based on the
        values from all the children.
        """
        pwr_ratio = max(c.power.value / c.power.range[1] for c in self.children.value)
        pwr = self.power.range[1] * pwr_ratio
        em = [0] * len(self.spectra.value)
        for child, idx in self._child_idx.items():
            # Compensate for the fact that not all children have the same max power
            if pwr > 0:
                pratio = child.power.value / pwr
            else:
                pratio = child.power.range[1] / self.power.range[1]
            for i, e in enumerate(child.emissions.value):
                em[idx + i] = e * pratio
                logging.debug("Read em %d as %s * %g W => %s * %g W",
                              idx + i, e, child.power.value, em[idx + i], pwr)
        return pwr, em

#     def _updateEmissions(self):
#         """
#         Called when the emission of one of the children changes.
#         Update the emissions from all the children
#         """
#         # TODO: do not call the setter in such case, but it's a little tricky
#         # because emissions is a ListVA, which has a special _set_value (which
#         # converts the list to a NotifyingList)
#         em = self._readEmissions()
#         if em != self.emissions.value:
#             self.emissions.value = em

    def _setEmissions(self, intensities):
        """
        intensities (list of N floats [0..1]): intensity of each source
        """
        if len(intensities) != len(self.spectra.value):
            raise ValueError("Emission must be an array of %d floats." % len(self.spectra.value))

        for child, idx in self._child_idx.items():
            em = intensities[idx:(idx + len(child.emissions.value))]
            pratio = self.power.range[1] / child.power.range[1] # >= 1
            cem = [min(max(0, e * pratio), 1) for e in em]
            logging.debug("Setting %s as %s * %g W => %s * %g W",
                          child.name, em, self.power.range[1], cem, child.power.range[1])
            child.emissions.value = cem

        # Read back the emissions, which might have been clamped
        pwr, em = self._readPwrEmissions()
        # TODO: what to do if power is different from the current value? That shouldn't happen, right?
        return em


class ExtendedLight(model.Emitter):
    """
    Wrapper component to add to an Emitter, a .period VA coming from a clock generator
    """
    def __init__(self, name, role, children, **kwargs):
        """
        children (dict str->Component): the two components to wrap together.
            The key must be "light" for the emitter component, and "clock" for the clock generator.
        """
        # This will create the .powerSupply VA
        model.Emitter.__init__(self, name, role, children=children, **kwargs)
        self._shape = ()

        # Determine child objects. Light
        try:
            self._light = children["light"]
        except KeyError:
            raise ValueError("No 'light' child provided")
        if not isinstance(self._light, model.ComponentBase):
            raise ValueError("Child %s is not an emitter." % (self._light.name,))
        if not model.hasVA(self._light, 'power'):
            raise ValueError("Child %s has no power VA." % (self._light.name,))
        if not model.hasVA(self._light, 'emissions'):
            raise ValueError("Child %s has no emissions VA." % (self._light.name,))
        # Clock generator
        try:
            self._clock = children["clock"]
        except KeyError:
            raise ValueError("No 'clock generator' child provided")
        if not isinstance(self._clock,  model.ComponentBase):
            raise ValueError("Child %s is not a Component." % (self._clock.name,))
        if not model.hasVA(self._clock, "period"):
            raise ValueError("Child %s has no period VA." % (self._clock.name,))

        # Only one VA from the clock
        self.period = self._clock.period

        # All the other VAs are straight from the light
        self.emissions = self._light.emissions
        self.spectra = self._light.spectra
        self.power = self._light.power

        # Turn off/on the power of the clock based on the light power
        self.emissions.subscribe(self._onEmissions)
        self.power.subscribe(self._onPower)

    def _updateClockPower(self, power, emissions):
        if any((em * power) > 0 for em in emissions):
            self._clock.power.value = 1
        else:
            self._clock.power.value = 0

    def _onEmissions(self, em):
        self._updateClockPower(self.power.value, em)

    def _onPower(self, power):
        self._updateClockPower(power, self.emissions.value)
