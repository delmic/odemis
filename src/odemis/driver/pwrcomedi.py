# -*- coding: utf-8 -*-
'''
Created on 17 Nov 2016

@author: Éric Piel

Copyright © 2016 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Uses a DAQ board with analog output accessed via the comedi interface to control
# an emitter power.
# This is designed for the USB-Dux board, but any comedi card with analog output
# should work.
# Note, with the USB-Dux D board, when the pwr_curve has voltages between 0->4 V,
# you should use pins 22->25 for channels 0->3  When the voltages are between
# -4 -> 4V, you should use pins 9->12.


import logging
from odemis import model
import odemis
from odemis.util import driver
import odemis.driver.comedi_simple as comedi
from past.builtins import long

class Light(model.Emitter):

    def __init__(self, name, role, device, channels, spectra, pwr_curve, **kwargs):
        """
        device (string): name of the /dev comedi  device (ex: "/dev/comedi0")
        channels (list of (0<=int)): The output channel for each source, as
          numbered in the comedi subdevice.
        spectra (list of 5-tuple of float): the spectra for each output channel used.
         Each tuple represents the wavelength in m for the 99% low, 25% low,
         centre/max, 25% high, 99% high. They do no have to be extremely precise.
         The most important is the centre, and that they are all increasing values.
        pwr_curve (list of dict (float -> 0<float)): Power curve segment map for
           each source. A segment map is a  series of voltage output on the
           analog output -> emission power of the light (W).
           It represents a series of linear segments to map the voltage output
           to the light emission. At least one pair should be provided.
           If no voltage is linked to 0W, then a 0V -> 0W mapping is used.
           The total curve should be monotonic.
        """
        # TODO: allow to give the unit of the power/pwr_curve ?

        model.Emitter.__init__(self, name, role, **kwargs)
        self._shape = ()

        try:
            self._device = comedi.open(device)
        #             self._fileno = comedi.fileno(self._device)
        except comedi.ComediError:
            raise ValueError("Failed to open DAQ device '%s'" % device)

        # Look for the analog output subdevice
        try:
            self._ao_subd = comedi.find_subdevice_by_type(self._device, comedi.SUBD_AO, 0)
            nchan = comedi.get_n_channels(self._device, self._ao_subd)
            if nchan < max(channels):
                raise ValueError("Device only has %d channels, while needed %d" % (nchan, max(channels)))
        except comedi.ComediError:
            raise ValueError("Failed to find an analogue output on DAQ device '%s'" % device)

        if len(channels) != len(spectra):
            raise ValueError("spectra argument should have the same length as channels (%d)" % len(channels))
        if len(channels) != len(pwr_curve):
            raise ValueError("pwr_curve argument should have the same length as channels (%d)" % len(channels))

        self._channels = channels

        # Check and store the power curves
        self._ranges = []
        self._pwr_curve = []
        for c, crv in zip(channels, pwr_curve):
            crv = [v for v in crv.items()]
            # Add 0W = 0V if nothing = 0W
            if 0 not in [w for v, w in crv]:
                crv.append((0, 0))
                logging.info("Adding 0V -> 0W mapping to pwr_curve for channel %d", c)
            # At least beginning and end values
            if len(crv) < 2:
                raise ValueError("pwr_curve for channel %d has less than 2 values: %s" % (c, crv))
            # Check it's monotonic
            crv = sorted(crv, key=lambda v: v[0])
            if crv[0][1] < 0:
                raise ValueError("pwr_curve for channel %d has negative power: %g W" % (c, crv[0][1]))
            if len(crv) != len(set(v for v, w in crv)):
                raise ValueError("pwr_curve for channel %d has identical voltages: %s" % (c, crv))
            if not all((crv[i][1] < crv[i + 1][1]) for i in range(len(crv) - 1)):
                raise ValueError("pwr_curve for channel %d is not monotonic: %s" % (c, crv))

            self._pwr_curve.append(crv)

            # Find the best range to use
            try:
                ri = comedi.find_range(self._device, self._ao_subd,
                                       c, comedi.UNIT_volt, crv[0][0], crv[-1][0])
            except comedi.ComediError:
                raise ValueError("Data range between %g and %g V is too high for hardware." %
                                 (crv[0][0], crv[-1][0]))
            self._ranges.append(ri)

        # Check the spectra
        spect = []  # list of the 5 wavelength points
        for c, wls in zip(channels, spectra):
            if len(wls) != 5:
                raise ValueError("Spectra for channel %d doesn't have exactly 5 wavelength points: %s" % (c, wls))
            if list(wls) != sorted(wls):
                raise ValueError("Spectra for channel %d has unsorted wavelengths: %s" % (c, wls))
            for wl in wls:
                if not 0 < wl < 100e-6:
                    raise ValueError("Spectra for channel %d has unexpected wavelength = %f nm"
                                     % (c, wl * 1e9))
            spect.append(tuple(wls))

        # Maximum power for channel to be used as a range for power
        max_power = tuple([crv[-1][1] for crv in self._pwr_curve])
        # Power value for each channel of the device
        self.power = model.ListContinuous(value=[0.] * len(self._channels),
                                          range=(tuple([0.] * len(self._channels)), max_power,),
                                          unit="W", cls=(int, long, float),)
        self.power.subscribe(self._updatePower)

        # info on which channel is which wavelength
        self.spectra = model.ListVA(spect, unit="m", readonly=True)

        # make sure everything is off (turning on the HUB will turn on the lights)
        self.power.value = self.power.range[0]

        self._metadata = {model.MD_HW_NAME: self.getHwName()}
        lnx_ver = driver.get_linux_version()
        self._swVersion = "%s (driver %s, linux %s)" % (odemis.__version__,
                                                        self.getSwVersion(),
                                                        ".".join("%s" % v for v in lnx_ver))
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_HW_VERSION] = self._hwVersion  # unknown

    def terminate(self):
        if self._device:
            # Make sure everything is powered off
            self.power.value = self.power.range[0]

            comedi.close(self._device)
            self._device = None

        super(Light, self).terminate()

    def _power_to_volt(self, power, curve):
        """
        power (0<float)
        curve (list of tuple (float, float)): the mapping between volt -> power
        return (float): voltage for outputting the given power
        raise: ValueError, if power requested if out of the power curve
        """
        if power < curve[0][1]:
            raise ValueError("Power requested %g < %g" % (power, curve[0][1]))

        # Find the segment that correspond to that power
        for i, (v, w) in enumerate(curve[1:]):
            if power <= w:
                seg = i
                break
        else:
            raise ValueError("Power requested %g > %g" % (power, curve[-1][1]))

        logging.debug("Converting %g W using segment %d: %s -> %s",
                      power, seg, curve[seg], curve[seg + 1])

        basev, basew = curve[seg]
        endv, endw = curve[seg + 1]

        ratio = (power - basew) / (endw - basew)
        v = basev + ratio * (endv - basev)
        return v

    def _volt_to_data(self, volt, channel, rngi):
        maxdata = comedi.get_maxdata(self._device, self._ao_subd, channel)
        rng = comedi.get_range(self._device, self._ao_subd, channel, rngi)
        d = comedi.from_phys(volt, rng, maxdata)
        return d

    # from semcomedi
    def getSwVersion(self):
        """
        Returns (string): displayable string showing the driver version
        """
        driver = comedi.get_driver_name(self._device)
        version = comedi.get_version_code(self._device)
        lversion = []
        for i in range(3):
            lversion.insert(0, version & 0xff)  # grab lowest 8 bits
            version >>= 8  # shift over 8 bits
        sversion = '.'.join(str(x) for x in lversion)
        return "%s v%s" % (driver, sversion)

    # from semcomedi
    def getHwName(self):
        """
        Returns (string): displayable string showing whatever can be found out
          about the actual hardware.
        """
        return comedi.get_board_name(self._device)

    def _updatePower(self, value):
        for c, r, crv, p in zip(self._channels, self._ranges, self._pwr_curve, value):
            p = min(p, crv[-1][1])
            v = self._power_to_volt(p, crv)
            d = self._volt_to_data(v, c, r)
            logging.debug("Setting channel %d to %d = %g V = %g W", c, d, v, p)
            comedi.data_write(self._device, self._ao_subd, c, r, comedi.AREF_GROUND, d)
