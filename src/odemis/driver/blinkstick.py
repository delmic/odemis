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
from blinkstick import blinkstick
import logging
from odemis import model
from odemis.model import HwError
import time
from past.builtins import long

class WhiteLed(model.Emitter):
    '''
    We describe the component that drives the BlinkStick led controller and
    performs the communication with the device. A series of leds is connected
    via the "RGB" channels of BlinkStick controller. It is considered that all
    channels are actually connected to white leds.
    The same intensity has to be set to each and every led of the series.
    '''

    def __init__(self, name, role, sn=None, max_power=0.1, inversed=False, **kwargs):
        """
        sn (None or str): serial number.
           If None, it will pick the first device found.
        max_power (0<float): maxium power emitted in W.
        """
        model.Emitter.__init__(self, name, role, **kwargs)

        self._sn = sn
        self._max_power = max_power

        # Just find the first BlinkStick led controller
        if sn is None:
            self._bstick = blinkstick.find_first()
        else:
            # Note: doesn't work with v1.1.7:
            # need fix on get_string(), reported here: https://github.com/arvydas/blinkstick-python/pull/35
            logging.warning("Using sn to select the device doesn't currently work")
            self._bstick = blinkstick.find_by_serial(sn)
        if self._bstick is None:
            raise HwError("Failed to find a Blinkstick for component %s. "
                          "Check that the device is connected to the computer."
                          % (name,))

        self._bstick.set_inverse(inversed)
        time.sleep(0.1)  # Device apparently needs some time to recover

        self._shape = ()
        # list of 5-tuples of floats
        self.spectra = model.ListVA([(380e-9, 390e-9, 560e-9, 730e-9, 740e-9)],
                                    unit="m", readonly=True)

        self.power = model.ListContinuous([0., ], ((0.,), (max_power,)), unit="W",
                                          cls=(int, long, float), setter=self._setPower)
        self.power.subscribe(self._updatePower, init=True)

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
        # TODO: check whether the device intensity is proportional
        intensity = int(round(value[0] * 255 / self._max_power))
        act_val = intensity * self._max_power / 255
        return [act_val]

    def _updatePower(self, value):
        intensity = int(round(value[0] * 255 / self._max_power))

        # All leds are connected to channel 0 and all 3 colours
        logging.debug("Led set to RGB=%d", intensity)
        self._bstick.set_color(0, 0, red=intensity, green=intensity, blue=intensity)

    def terminate(self):
        if self._bstick is not None:
            self._bstick.turn_off()
            self._bstick = None

        super(WhiteLed, self).terminate()

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
