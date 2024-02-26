#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created 23 September 2022

@author: Éric Piel

Copyright © 2022-2023 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

Ensure the SPARC spectrograph is powered and turned on, and then select a specific turret.
To start automatically with Odemis, run with:
sh -c "env PYTHONPATH=$HOME/development/odemis/src/ $HOME/development/odemis/scripts/odemis-start-turret.py 1 && odemis-start"
"""


import logging
import sys
import os
from odemis.driver import powerctrl, andorshrk

logging.getLogger().setLevel(logging.INFO)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")

TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to real HW

KWARGS_PCU = {
    "name": "Power Control Unit",
    "role": "power-control",
    "port": "/dev/ttyPMT*",
    "pin_map": {
        "Spectrograph": 2,
    },
    "delay": { # Time it takes before a component is accessible
        "Spectrograph": 90, # SR-193 needs a looong time to initialise
    },
    #"ids": [],
    "check_power": False,  # Works even if the PCU doesn't detect power (due to missing EEPROMs or issue with the EEPROM reading)
}

KWARGS_SHRK = {
    "name": "Spectrograph",
    "role": "spectrograph",
    "device": 0, # "KY-4237",
}

if TEST_NOHW:
    # Test using the simulator
    KWARGS_PCU["port"] ="/dev/fake"
    KWARGS_PCU["delay"]["Spectrograph"] = 3  # s
    KWARGS_SHRK["device"] = "fake"

def main(args):
    turret = int(args[1])
    try:
        pcu = powerctrl.PowerControlUnit(**KWARGS_PCU)
        logging.info("Turning on spectrograph... (2 min)")
        pcu.supply({"Spectrograph": True}).result()
        spg = andorshrk.Shamrock(**KWARGS_SHRK)
        spg.SetTurret(turret)
        gchoices = spg._getGratingChoices()
        logging.info("Switched to turret %d, with gratings: %s", turret, ", ".join(gchoices.values()))
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 130

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
