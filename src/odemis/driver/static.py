# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012-2015 Éric Piel, Delmic

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

# Provides various components which are not actually drivers but just representing
# physical components which cannot be modified by software. It's mostly used for
# computing the right metadata/behaviour of the system.

from collections.abc import Iterable
import math
import numbers
from numpy.polynomial import polynomial
from odemis import model
import odemis
from odemis.model import isasync

# dictionary that relates the attibute names related to the mirror with their corresponding vigilant attributes
CONFIG_2_VA = {"pole_pos": "polePosition", "focus_dist": "focusDistance", "hole_diam": "holeDiameter", "parabola_f": "parabolaF", "x_max": "xMax"}
# TODO what is the best type? Emitter? Or something else?
# Detector needs a specific .data and .shape
class OpticalLens(model.HwComponent):
    """
    A class which represents either just a lens with a given magnification,
    or the parabolic mirror and lens of a SPARC.
    It should "affect" the detector on which it's in front of.
    """

    def __init__(self, name, role, mag, mag_choices=None, na=0.95, ri=1,
                 pole_pos=None, mirror_pos_top=None, mirror_pos_bottom=None,
                 x_max=None, hole_diam=None,
                 focus_dist=None, parabola_f=None, rotation=None,
                 configurations=None, **kwargs):
        """
        name (string): should be the name of the product (for metadata)
        mag (float > 0): magnification ratio
        mag_choices (None, list of floats > 0): list of allowed magnification ratio.
          If None, the magnification will be allowed for any value between 1e-3 to 1e6.
        na (float > 0): numerical aperture
        ri (0.01 < float < 100): refractive index
        pole_pos (2 floats >= 0): position of the pole on the CCD (in px, without
          binning, with the top-left pixel as origin).
          Used for angular resolved imaging on SPARC (only). cf MD_AR_POLE
        mirror_pos_top (2 floats): position of the top of the mirror dependent
          of the wavelength. It's defined in px and px/m, without binning, with
          the top-left pixel as origin. With the 2 floats named a and b, the line
          is defined as a + b * wl (wavelength is in m)
          cf MD_AR_MIRROR_TOP
        mirror_pos_bottom (2 floats): same as mirror_pos_top, but for the bottom.
          cf MD_AR_MIRROR_BOTTOM
        x_max (float): the distance between the parabola origin and the cutoff
          position (in meters). Used for angular resolved imaging on SPARC (only).
          cf MD_AR_XMAX
        hole_diam (float): diameter of the hole in the mirror (in meters). Used
          for angular resolved imaging on SPARC (only).
          cf MD_AR_HOLE_DIAMETER
        focus_dist (float): the vertical mirror cutoff, iow the min distance
          between the mirror and the sample (in meters). Used for angular
          resolved imaging on SPARC (only).
          cf MD_AR_FOCUS_DISTANCE
        parabola_f (float): parabola_parameter=1/4f. Used for angular
          resolved imaging on SPARC (only).
          cf MD_AR_PARABOLA_F
        rotation (0<float<2*pi): rotation between the Y axis of the SEM
          referential and the optical path axis. Used on the SPARC to report
          the rotation between the AR image and the SEM image.
        configurations (dict str -> (dict str -> value)): {configuration name -> {attribute name -> value}}
          All the configurations supported and their settings. A "configuration" is a set of attributes with
          predefined values. When this argument is specified, a .configuration attribute will be available,
          with each configuration name, and changing it will automatically set all the associated attributes
          to their predefined value.
        """
        assert (mag > 0)
        model.HwComponent.__init__(self, name, role, **kwargs)

        self._swVersion = "N/A (Odemis %s)" % odemis.__version__
        self._hwVersion = name

        # allow the user to modify the value, if the lens is manually changed
        if mag_choices is None:
            self.magnification = model.FloatContinuous(mag, range=(1e-3, 1e6), unit="")
        else:
            mag_choices = frozenset(mag_choices)
            if mag not in mag_choices:
                raise ValueError("mag (%s) is not within the mag_choices %s" %
                                 (mag, mag_choices))
            self.magnification = model.FloatEnumerated(mag, choices=mag_choices, unit="")

        self.numericalAperture = model.FloatContinuous(na, range=(1e-6, 1e3), unit="")
        self.refractiveIndex = model.FloatContinuous(ri, range=(0.01, 10), unit="")

        if pole_pos is not None:
            # Use 1 million as the arbitrary max value (increase if you have a bigger CCD!)
            if (not isinstance(pole_pos, Iterable) or
                len(pole_pos) != 2 or any(not 0 <= v < 1e6 for v in pole_pos)):
                raise ValueError("pole_pos must be 2 positive values, got %s" % pole_pos)
            self.polePosition = model.TupleContinuous(tuple(pole_pos),
                                                      range=((0, 0), (1e6, 1e6)),
                                                      cls=numbers.Real,
                                                      unit="px")
        # mirrorPositionTop and mirrorPositionBottom are similar. They represent
        # a line on the CCD as a function of the wavelength (increasing along the X axis).
        # So it's two values, a & b, which define a line as px = a + b * wl.
        # Where px has origin at the top of the CCD. The typical image has a field
        # in the order ~100 nm. So the a & b values can get quite large, especially
        # when the user is playing with drawing the lines. Hence, we put very large
        # range. The main goal of using a TupleContinuous over a TupleVA is to
        # check it's 2 floats.
        if mirror_pos_top is not None:
            if (not isinstance(pole_pos, Iterable) or
                len(pole_pos) != 2 or any(not isinstance(v, numbers.Real) for v in mirror_pos_top)):
                raise ValueError("pole_pos must be 2 floats, got %s" % mirror_pos_top)
            self.mirrorPositionTop = model.TupleContinuous(tuple(mirror_pos_top),
                                                      range=((-1e18, -1e18), (1e18, 1e18)),
                                                      cls=numbers.Real,
                                                      unit="px, px/m")
        if mirror_pos_bottom is not None:
            if (not isinstance(pole_pos, Iterable) or
                len(pole_pos) != 2 or any(not isinstance(v, numbers.Real) for v in mirror_pos_bottom)):
                raise ValueError("pole_pos must be 2 floats, got %s" % mirror_pos_bottom)
            self.mirrorPositionBottom = model.TupleContinuous(tuple(mirror_pos_bottom),
                                                      range=((-1e18, -1e18), (1e18, 1e18)),
                                                      cls=numbers.Real,
                                                      unit="px, px/m")

        if x_max is not None:
            self.xMax = model.FloatVA(x_max, unit="m")
        if hole_diam is not None:
            self.holeDiameter = model.FloatVA(hole_diam, unit="m")
        if focus_dist is not None:
            self.focusDistance = model.FloatVA(focus_dist, unit="m")
        if parabola_f is not None:
            self.parabolaF = model.FloatVA(parabola_f, unit="m")
        if rotation is not None:
            # In theory, we only allow between 0 and 2 Pi. But haven't enforced it
            # for a long time, and honestly for small negative rotation, it's easier
            # to read if it's negative. So automatically fit it within the range.
            rotation %= 2 * math.pi
            self.rotation = model.FloatContinuous(rotation, (0, 2 * math.pi),
                                                  unit="rad")
        if configurations is not None:
            self._configurations = configurations
            # Find the configuration which is closest to the current settings
            def _compare_config(cn):
                settings = configurations[cn]
                score = 0
                for arg, value in settings.items():
                    try:
                        vaname = CONFIG_2_VA[arg]
                        va = getattr(self, vaname)
                    except (KeyError, AttributeError):
                        raise ValueError("Attribute name predefined in the configuration required")
                    if value == va.value:
                        score += 1
                return score
            current_conf = max(configurations, key=_compare_config)
            self.configuration = model.StringEnumerated(current_conf, choices=set(configurations.keys()), setter=self._setConfiguration)

    def _setConfiguration(self, config):
        # set all the VAs based on the ._configurations
        settings = self._configurations[config]
        for arg, value in settings.items():
            vaname = CONFIG_2_VA[arg]
            va = getattr(self, vaname)
            va.value = value

        return config

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
        if not isinstance(band, Iterable) or len(band) == 0:
            raise ValueError("band must be a (list of a) list of 2 floats")
        # is it a list of list?
        if isinstance(band[0], Iterable):
            # => set of 2-tuples
            for sb in band:
                if len(sb) != 2:
                    raise ValueError("Expected only 2 floats in band, found %d" % len(sb))
            band = tuple(tuple(b) for b in band)
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

    def stop(self, axes=None):
        pass  # nothing to stop


class Spectrograph(model.Actuator):
    """
    A very simple spectrograph component for spectrographs which cannot be
    controlled by software.
    Just provide the wavelength list describing the light position on the CCD
    according to the center wavelength specified.
    """
    def __init__(self, name, role, wlp, children=None, **kwargs):
        """
        wlp (list of floats): polynomial for conversion from distance from the
          center of the CCD to wavelength (in m):
          w = wlp[0] + wlp[1] * x + wlp[2] * x²... 
          where w is the wavelength (in m), x is the position from the center
          (in m, negative are to the left), and p is the polynomial
          (in m, m^0, m^-1...). So, typically, a first order
          polynomial contains as first element the center wavelength, and as
          second element the light dispersion (in m/m)
        """
        if kwargs.get("inverted", None):
            raise ValueError("Axis of spectrograph cannot be inverted")

        if not isinstance(wlp, list) or len(wlp) < 1:
            raise ValueError("wlp need to be a list of at least one float")

        # Note: it used to need a "ccd" child, but not anymore
        self._swVersion = "N/A (Odemis %s)" % odemis.__version__
        self._hwVersion = name

        self._wlp = wlp
        pos = {"wavelength": self._wlp[0]}
        wla = model.Axis(range=(0, 2400e-9), unit="m")
        model.Actuator.__init__(self, name, role, axes={"wavelength": wla},
                                **kwargs)
        self.position = model.VigilantAttribute(pos, unit="m", readonly=True)

    # We simulate the axis, to give the same interface as a fully controllable
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

    def getPixelToWavelength(self, npixels, pxs):
        """
        Return the lookup table pixel number of the CCD -> wavelength observed.
        npixels (1 <= int): number of pixels on the CCD (horizontally), after
          binning.
        pxs (0 < float): pixel size in m (after binning)
        return (list of floats): pixel number -> wavelength in m
        """
        pl = list(self._wlp)
        pl[0] = self.position.value["wavelength"]

        # This polynomial is from m (distance from centre) to m (wavelength),
        # but we need from px (pixel number on spectrum) to m (wavelength). So
        # we need to convert by using the density and quantity of pixels
        # wl = pn(x)
        # x = a + bx' = pn1(x')
        # wl = pn(pn1(x')) = pnc(x')
        # => composition of polynomials
        # with "a" the distance of the centre of the left-most pixel to the
        # centre of the image, and b the density in meters per pixel.
        # distance from the pixel 0 to the centre (in m)
        distance0 = -(npixels - 1) / 2 * pxs
        pnc = self.polycomp(pl, [distance0, pxs])

        npn = polynomial.Polynomial(pnc,  # pylint: disable=E1101
                                    domain=[0, npixels - 1],
                                    window=[0, npixels - 1])
        return npn.linspace(npixels)[1]

    @staticmethod
    def polycomp(c1, c2):
        """
        Compose two polynomials : c1 o c2 = c1(c2(x))
        The arguments are sequences of coefficients, from lowest order term to highest, e.g., [1,2,3] represents the polynomial 1 + 2*x + 3*x**2.
        """
        # TODO: Polynomial(Polynomial()) seems to do just that?
        # using Horner's method to compute the result of a polynomial
        cr = [c1[-1]]
        for a in reversed(c1[:-1]):
            # cr = cr * c2 + a
            cr = polynomial.polyadd(polynomial.polymul(cr, c2), [a])

        return cr

    
class TimeCorrelator(model.Detector):
    """
    A very simple Time Correlator that stands in for a Symphotime Controller. Allows one
    to run FLIM without running the Symphotime simulator.
    """

    def __init__(self, name, role, **kwargs):
        super(TimeCorrelator, self).__init__(name, role, **kwargs)
        self.data = model.DataFlow()
        # Data depth is 0, as we don't get the data
        self._shape = (0,)
