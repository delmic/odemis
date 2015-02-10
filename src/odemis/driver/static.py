# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
from Pyro4.core import isasync
from odemis import model
import collections
import odemis

"""
Provides various components which are not actually drivers but just representing
physical components which cannot be modified by software. It's mostly used for
computing the right metadata/behaviour of the system.
"""

# TODO what is the best type? Emitter? Or something else?
# Detector needs a specific .data and .shape
class OpticalLens(model.HwComponent):
    """
    A very simple class which just represent a lens with a given magnification.
    It should "affect" the detector on which it's in front of.
    """
    def __init__(self, name, role, mag, pole_pos=None, **kwargs):
        """
        name (string): should be the name of the product (for metadata)
        mag (float > 0): magnification ratio
        pole_pos (2 floats > 0): position of the pole on the CCD (in px, without
          binning). Used for angular resolved imaging on SPARC (only).
          cf MD_AR_POLE
        """
        assert (mag > 0)
        model.HwComponent.__init__(self, name, role, **kwargs)

        self._swVersion = "N/A (Odemis %s)" % odemis.__version__
        self._hwVersion = name

        # allow the user to modify the value, if the lens is manually changed
        self.magnification = model.FloatContinuous(mag, range=[1e-3, 1e6], unit="")

        if pole_pos is not None:
            if (not isinstance(pole_pos, collections.Iterable) or
                len(pole_pos) != 2 or any(v < 0 for v in pole_pos)):
                raise ValueError("pole_pos must be 2 positive values, got %s" % pole_pos)
            self.polePosition = model.ResolutionVA(pole_pos,
                                                   rng=[(0, 0), (1e6, 1e6)])

class LightFilter(model.Actuator):
    """
    A very simple class which just represent a light filter (blocks a wavelength 
    band).
    It should "affect" the detector on which it's in front of, if it's filtering
    the "out" path, or "affect" the emitter in which it's after, if it's
    filtering the "in" path.
    """
    def __init__(self, name, role, band, **kwargs):
        """
        name (string): should be the name of the product (for metadata)
        band ((list of) 2-tuple of float > 0): (m) lower and higher bound of the
          wavelength of the light which goes _through_. If it's a list, it implies
          that the filter is multi-band.
        """
        # One enumerated axis: band
        # Create a 2-tuple or a set of 2-tuples
        if not isinstance(band, collections.Iterable) or len(band) == 0:
            raise ValueError("band must be a (list of a) list of 2 floats")
        # is it a list of list?
        if isinstance(band[0], collections.Iterable):
            # => set of 2-tuples
            for sb in band:
                if len(sb) != 2:
                    raise ValueError("Expected only 2 floats in band, found %d" % len(sb))
            band = tuple(band)
        else:
            # 2-tuple
            if len(band) != 2:
                raise ValueError("Expected only 2 floats in band, found %d" % len(band))
            band = (tuple(band),)

        # Check the values are min/max and in m: typically within nm (< µm!)
        max_val = 10e-6 # m
        for low, high in band:
            if low > high:
                raise ValueError("Min of band must be first in list")
            if low > max_val or high > max_val:
                raise ValueError("Band contains very high values for light "
                     "wavelength, ensure the value is in meters: %r." % band)

        # TODO: have the position as the band value?
        band_axis = model.Axis(choices={0: band})

        model.Actuator.__init__(self, name, role, axes={"band": band_axis}, **kwargs)
        self._swVersion = "N/A (Odemis %s)" % odemis.__version__
        self._hwVersion = name

        # Will always stay at position 0
        self.position = model.VigilantAttribute({"band": 0}, readonly=True)

        # TODO: MD_OUT_WL or MD_IN_WL depending on affect
        self._metadata = {model.MD_FILTER_NAME: name,
                          model.MD_OUT_WL: band}

    def getMetadata(self):
        return self._metadata

    def moveRel(self, shift):
        return self.moveAbs(shift) # shift must be 0 => same as moveAbs

    def moveAbs(self, pos):
        if pos != {"band": 0}:
            raise ValueError("Unsupported position %s" % pos)
        return model.InstantaneousFuture()



class Spectrograph(model.Actuator):
    """
    A very simple spectrograph component for spectrographs which cannot be 
    controlled by software.
    Just get a polynomial describing the light position on the CCD as init,
    and an axis which  
    """
    def __init__(self, name, role, wlp, **kwargs):
        """
        wlp (list of floats): polynomial for conversion from distance from the 
          center of the CCD to wavelength (in m). So, typically, a first order
          polynomial contains as first element the center wavelength, and as
          second element the light dispersion (in m/m).
        """
        if kwargs.get("inverted", None):
            raise ValueError("Axis of spectrograph cannot be inverted")

        if not isinstance(wlp, list) or len(wlp) < 1:
            raise ValueError("wlp need to be a list of at least one float")

        self._swVersion = "N/A (Odemis %s)" % odemis.__version__
        self._hwVersion = name

        self._wlp = wlp
        pos = {"wavelength": self._wlp[0]}
        wla = model.Axis(range=(0, 2400e-9), unit="m")
        model.Actuator.__init__(self, name, role, axes={"wavelength": wla},
                                **kwargs)
        self.position = model.VigilantAttribute(pos, unit="m", readonly=True)


    # we simulate the axis, to give the same interface as a fully controllable
    # spectrograph, but it has to actually reflect the state of the hardware.
    @isasync
    def moveRel(self, shift):
        # convert to a call to moveAbs
        new_pos = {}
        for axis, value in shift.items():
            new_pos[axis] = self.position.value[axis] + value
        return self.moveAbs(new_pos)

    @isasync
    def moveAbs(self, pos):
        for axis, value in pos.items():
            if axis == "wavelength":
                # it's read-only, so we change it via _value
                self.position._value[axis] = value
                self.position.notify(self.position.value)
            else:
                raise LookupError("Axis '%s' doesn't exist" % axis)

        return model.InstantaneousFuture()

    def stop(self, axes=None):
        # nothing to do
        pass

    def getPolyToWavelength(self):
        """
        Compute the right polynomial to convert from a position on the sensor to the
          wavelength detected. It depends on the current grating, center 
          wavelength (and focal length of the spectrometer). 
        Note: It will return the polynomial given as init + the shift from the
          original center wavelength.
        returns (list of float): polynomial coefficients to apply to get the current
          wavelength corresponding to a given distance from the center: 
          w = p[0] + p[1] * x + p[2] * x²... 
          where w is the wavelength (in m), x is the position from the center
          (in m, negative are to the left), and p is the polynomial (in m, m^0, m^-1...).
        """
        pl = list(self._wlp)
        pl[0] = self.position.value["wavelength"]
        return pl
