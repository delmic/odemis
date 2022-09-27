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
import logging
from odemis import model
from past.builtins import long
import copy

class MultiplexLight(model.Emitter):
    """
    Light composed of multiple Lights
    """
    def __init__(self, name, role, dependencies, **kwargs):
        """
        dependencies (dict str -> Emitter): arbitrary role -> emitter to be used as
          part of this emitter. All its provided emissions will be provided.
        """
        # TODO: allow to only use a subset of the emissions from each child

        if not dependencies:
            raise ValueError("MultiplexLight needs dependencies")

        model.Emitter.__init__(self, name, role, dependencies=dependencies, **kwargs)
        self._shape = ()

        self._child_idx = {} # Emitter -> index (shift) in the power/spectra

        spectra = []
        min_power = []
        max_power = []
        for n, child in dependencies.items():
            if not (model.hasVA(child, "power") and
                    model.hasVA(child, "spectra")
                   ):
                raise ValueError("Child %s is not a light emitter" % (n,))
            self._child_idx[child] = len(spectra)
            spectra.extend(child.spectra.value)
            min_power.extend(child.power.range[0])
            max_power.extend(child.power.range[1])
            # Subscribe to each child power to update self.power
            child.power.subscribe(self._updateMultiplexPower)

        # Child with the maximum power range
        self.power = model.ListContinuous(value=[0] * len(spectra),
                                          range=(tuple(min_power), tuple(max_power)),
                                          unit="W", cls=(int, long, float))
        self.power.subscribe(self._setChildPower)
        self._updateMultiplexPower(None)
        # info on which source is which wavelength
        self.spectra = model.ListVA(spectra, unit="m", readonly=True)

    def _setChildPower(self, power):
        """
        Whenever the self.power changes, the .power of the corresponding dependency is adjusted
        """
        # Update child powers with new values
        power = copy.copy(power)  # Not to be attached to self.power.value
        for child, idx in self._child_idx.items():
            # Prevent re-updating child power if power values are the same
            # To avoid infinite loop from alternating subscriber calls
            cpwr = power[idx:idx+len(child.power.value)]
            if child.power.value != cpwr:
                child.power.value = cpwr
            logging.debug("Setting %s to %s W", child.name, cpwr)

    def _updateMultiplexPower(self, _):
        """
        Whenever the .power of a dependency changes, the self.power is updated appropriately
        """
        pwr = list(self.power.value)
        for child, idx in self._child_idx.items():
            pwr[idx:idx + len(child.power.value)] = child.power.value[:]
        self.power.value = pwr


class ExtendedLight(model.Emitter):
    """
    Wrapper component to add to an Emitter, a .period VA coming from a clock generator
    """

    def __init__(self, name, role, dependencies, **kwargs):
        """
        dependencies (dict str->Component): the two components to wrap together.
            The key must be "light" for the emitter component, and "clock" for the clock generator.
        """
        # This will create the .powerSupply VA
        model.Emitter.__init__(self, name, role, dependencies=dependencies, **kwargs)
        self._shape = ()

        # Determine child objects. Light
        try:
            self._light = dependencies["light"]
        except KeyError:
            raise ValueError("No 'light' child provided")
        if not isinstance(self._light, model.ComponentBase):
            raise ValueError("Child %s is not an emitter." % (self._light.name,))
        if not model.hasVA(self._light, 'power'):
            raise ValueError("Child %s has no power VA." % (self._light.name,))

        # Clock generator
        try:
            self._clock = dependencies["clock"]
        except KeyError:
            raise ValueError("No 'clock generator' child provided")
        if not isinstance(self._clock,  model.ComponentBase):
            raise ValueError("Child %s is not a Component." % (self._clock.name,))
        if not model.hasVA(self._clock, "period"):
            raise ValueError("Child %s has no period VA." % (self._clock.name,))

        # Only one VA from the clock
        self.period = self._clock.period

        # All the other VAs are straight from the light
        self.spectra = self._light.spectra
        self.power = self._light.power

        # Turn off/on the power of the clock based on the light power
        self.power.subscribe(self._onPower)

    def _onPower(self, power):
        """
        Update clock power if any power source is activated
        """
        if any(p > 0 for p in power):
            self._clock.power.value = 1
        else:
            self._clock.power.value = 0
