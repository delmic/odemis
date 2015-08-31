# -*- coding: utf-8 -*-
'''
Created on 10 Jul 2015

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
from __future__ import division, absolute_import

from blinkstick import blinkstick
import logging
from odemis import model
from odemis.model import HwError
import time

INTENSITY_RANGE = (0, 255)


class WhiteLed(model.Emitter):
    '''
    We describe the component that drives the BlinkStick led controller and
    performs the communication with the device. This controller will be used to
    control the white led configuration in SECOM. A series of leds is connected
    via the same channel to one of the RGB outputs of BlinkStick controller (we
    assume red). The same intensity has to be set to each and every led of the
    series.
    '''

    def __init__(self, name, role, sn=None, **kwargs):
        """
        sn (None or str): serial number.
           If None, it will pick the first device found.
        """
        model.Emitter.__init__(self, name, role, **kwargs)

        self._sn = sn

        # Just find the first BlinkStick led controller
        if sn is None:
            self._bstick = blinkstick.find_first()
        else:
            # Note: doesn't work with v1.1.7:
            # need fix on get_string(), reported here: https://github.com/arvydas/blinkstick-python/pull/35
            self._bstick = blinkstick.find_by_serial(sn)
        if self._bstick is None:
            raise HwError("Failed to find a Blinkstick for component %s. "
                          "Check that the device is connected to the computer."
                          % (name,))

        # TODO: check if inverse mode (1) is needed
        self._bstick.set_mode(0)
        time.sleep(0.1)  # Device apparently needs some time to recover

        self._shape = ()
        # TODO: allow to change the power also via emissions
        self.emissions = model.ListVA([1.0], unit="", setter=lambda x: [1.0])
        # list of 5-tuples of floats
        self.spectra = model.ListVA([(380e-9, 390e-9, 560e-9, 730e-9, 740e-9)],
                                    unit="m", readonly=True)

        # FIXME: Find actual range, or allow the user to indicate it?
        self._max_power = 0.4  # W
        self.power = model.FloatContinuous(0., (0., self._max_power), unit="W",
                                           setter=self._setPower)
        self._setPower(0)

        self._swVersion = "Blinkstick v%s" % (blinkstick.__version__,)
        # These functions report wrong values on Linux with v1.1.7
#         man = self._bstick.get_manufacturer()
#         desc = self._bstick.get_description()
#         rsn = self._bstick.get_serial()
        man = self._bstick.device.manufacturer
        desc = self._bstick.device.product
        rsn = self._bstick.device.serial_number
        self._hwVersion = "%s %s (s/n: %s)" % (man, desc, rsn)

    def _setPower(self, value):
        # Calculate the corresponding intensity (0 -> 255) for the power given
        intensity = int(round(value * INTENSITY_RANGE[1] / self._max_power))

        # All leds are connected to channel 0 and colour red
        logging.debug("Led %d set to red=%d", 0, intensity)
        self._bstick.set_color(0, 0, red=intensity)
        self._metadata[model.MD_LIGHT_POWER] = self.power.value

        act_val = intensity * self._max_power / INTENSITY_RANGE[1]
        return act_val

    def terminate(self):
        if self._bstick is not None:
            self._bstick.turn_off()
            self._bstick = None

    @staticmethod
    def scan():
        """
        returns (list of 2-tuple): name, kwargs
        Note: it's obviously not advised to call this function if a device is already under use
        """
        logging.info("Looking for blinksticks...")
        found = []  # (list of 2-tuple): name, kwargs
        for d in blinkstick.find_all():
            found.append(("Blinkstick led", {"sn": d.device.serial_number}))

        return found
