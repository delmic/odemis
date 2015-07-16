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
from __future__ import division

from blinkstick import blinkstick
import logging
from odemis import model
from odemis.driver.tlfw import HwError
import time

INTENSITY_RANGE = [0, 255]


class WhiteLed(model.Emitter):
    '''
    We describe the component that drives the BlinkStick led controller and
    performs the communication with the device. This controller will be used to
    control the white led configuration in SECOM. A series of leds is connected
    via the same channel to one of the RGB outputs of BlinkStick controller (we
    assume red). The same intensity has to be set to each and every led of the
    series. Additionally we provide a fake version of this component that
    imitates the operation and behavior of the actual BlinkStick for testing
    purposes.
    '''

    def __init__(self, name, role, no_leds=0, **kwargs):
        """
        no_leds (int): number of leds connected via BlinkStick
        """
        model.Emitter.__init__(self, name, role, **kwargs)

        self.no_leds = no_leds

        # Just find the first BlinkStick led controller
        self.bstick = blinkstick.find_first()
        if self.bstick is None:
            raise HwError("Failed to find a WhiteLed controller. "
                          "Check that the device is connected to the computer.")

        self._shape = ()
        # FIXME: Find actual range
        self._max_power = 0.4  # W
        self.power = model.FloatContinuous(0., (0., self._max_power), unit="W",
                                           setter=self._setPower)
        self.bstick.set_mode(1)  # Just to set the intensity in ascending order
        time.sleep(1)

    def getMetadata(self):
        metadata = {}
        metadata[model.MD_LIGHT_POWER] = self.power.value
        return metadata

    def _setPower(self, value):
        logging.debug("WhiteLed power set to %f W", value)
        # Calculate the corresponding intensity ([0,255]) for the power given
        intensity = int((value / (self.power.range[1] - self.power.range[0])) *
                        (INTENSITY_RANGE[1] - INTENSITY_RANGE[0]))
        for i in range(self.no_leds):
            # All leds are connected to channel 0 and colour red
            self.bstick.set_color(0, i, red=intensity)

        return value

    def terminate(self):
        if self.bstick is not None:
            self.bstick.turn_off()


class FakeWhiteLed(model.Emitter):
    '''
    Fake component that simulates the WhiteLed behavior.
    '''

    def __init__(self, name, role, no_leds=0, **kwargs):
        """
        no_leds (int): number of leds connected via BlinkStick
        """
        model.Emitter.__init__(self, name, role, **kwargs)

        self._shape = ()
        self._max_power = 0.4  # W (According to doc: ~400mW)
        self.power = model.FloatContinuous(0., (0., self._max_power), unit="W")

    def getMetadata(self):
        metadata = {}
        metadata[model.MD_LIGHT_POWER] = self.power.value
        return metadata
