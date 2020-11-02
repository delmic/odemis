# -*- coding: utf-8 -*-
'''
Created on 12 Mar 2012

@author: Éric Piel

Copyright © 2012-2016 Éric Piel, Delmic

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

# in case it's need to find all the devices supported (e.g., for a scan)
__all__ = ["andorcam3", "andorcam2", "andorshrk", "pi", "pigcs", "lle",
           "semcomedi", "spectrapro", "pvcam", "omicronxx", "tlfw", "tlaptmf",
           "tmcm", "phenom", "nfpm", "tescan", "pmtctrl", "powerctrl",
           "blinkstick", "picoquant", "ueye", "pwrcomedi", "smaract",
           # Modules that do not support scanning because that wouldn't make sense:
           "actuator", "scanner", "spectrometer", "simcam", "simsem", "emitter",
           "simulated", "static"]
