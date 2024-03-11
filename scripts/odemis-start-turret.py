#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created 23 September 2022

@author: Éric Piel

Copyright © 2022-2024 Éric Piel, Delmic

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
sh -c "env PYTHONPATH=$HOME/development/odemis/src/ $HOME/development/odemis/scripts/odemis-start-turret.py 1 --notify 'Turret 300nm/mirror' && odemis-start"
"""

import logging
import os
import sys

import notify2

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
    "delay": {  # Time it takes before a component is accessible
        "Spectrograph": 90,  # SR-193 needs a looong time to initialise
    },
    #"ids": [],
    "check_power": False,  # Works even if the PCU doesn't detect power (due to missing EEPROMs or issue with the EEPROM reading)
}

KWARGS_SHRK = {
    "name": "Spectrograph",
    "role": "spectrograph",
    "device": 0,  # or use serial number, like "KY-4237"
}

if TEST_NOHW:
    # Test using the simulator
    KWARGS_PCU["port"] ="/dev/fake"
    KWARGS_PCU["delay"]["Spectrograph"] = 3  # s
    KWARGS_SHRK["device"] = "fake"


def main(args):
    turret = int(args[1])
    notify = "--notify" in args[1:]
    if len(args) >= 4:
        name = args[3]
    else:
        name = f"turret {turret}"
    try:
        pcu = powerctrl.PowerControlUnit(**KWARGS_PCU)
        logging.info("Turning on spectrograph... (2 min)")
        if notify:
            notify2.init("Odemis")
            notif = notify2.Notification("Starting Odemis", f"Selecting spectrograph {name}", icon="odemis")
            notif.show()

        # Trick: if the spectrograph power is already on (eg, because this script was just run, but
        # with the wrong turret number), let's assume it's already been on for a long time. In this
        # case, no need to turn it on again, which saves 90s. In the very unlikely case that the spectrograph
        # was just turned on, typically the driver will wait for the spectrograph to be ready anyway.
        if not pcu.supplied.value["Spectrograph"]:
            pcu.supply({"Spectrograph": True}).result()
        else:
            logging.info("Spectrograph already on, assuming it's ready")

        spg = andorshrk.Shamrock(**KWARGS_SHRK)
        spg.SetTurret(turret)
        gchoices = spg._getGratingChoices()
        logging.info("Switched to turret %d (%s), with gratings: %s", turret, name, ", ".join(gchoices.values()))
        if notify:
            notif.close()
    except Exception as ex:
        logging.exception("Unexpected error while performing action.")
        if notify:
            notif = notify2.Notification("Error starting Odemis", str(ex), icon="dialog-warning")
            notif.show()
        return 130

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
