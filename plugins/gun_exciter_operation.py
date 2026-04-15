# -*- coding: utf-8 -*-
"""
Created on 15 Apr 2026

@author: Éric Piel

Copyright © 2026 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

# This plugin automatically applies settings values to the e-beam gun exciter VAs whenever
# the "operation" VA changes. The values are read from the component's MD_CALIB
# metadata, which maps operation mode names to dictionaries of VA name → value.
# For example:
#   MD_CALIB = {
#       'operation': {
#           'CW':     {'filamentCurrent': 3.05, 'extractorVoltage': 4200, ...},
#           'Pulsed': {'filamentCurrent': 1.45, 'extractorVoltage': 3900, ...},
#       }
#   }
# If the selected operation mode is not present in the calibration (e.g. "User defined"),
# the VAs are left unchanged so the user can set them manually.

import logging

from odemis import model
from odemis.gui.plugin import Plugin


class GunExciterOperationPlugin(Plugin):
    name = "E-beam Gun Exciter Operation Loader"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope: model.Microscope, main_app) -> None:
        """
        Initialise the plugin.

        :param microscope: the main back-end microscope component.
        :param main_app: the main GUI application object.
        """
        super().__init__(microscope, main_app)

        main_data = self.main_app.main_data
        gun_exciter = main_data.ebeam_gun_exciter
        if gun_exciter is None:
            logging.debug("%s plugin not loaded: no ebeam-gun-exciter component", self.name)
            return

        if not model.hasVA(gun_exciter, "operation"):
            logging.info("%s plugin not loaded: ebeam-gun-exciter has no 'operation' VA", self.name)
            return

        md = gun_exciter.getMetadata()
        if not isinstance(md.get(model.MD_CALIB), dict):
            logging.info("%s plugin not loaded: MD_CALIB is missing", self.name)
            return

        self._gun_exciter = gun_exciter
        gun_exciter.operation.subscribe(self._on_operation)
        logging.debug("%s plugin loaded, monitoring operation VA", self.name)

    def _on_operation(self, operation: str) -> None:
        """
        Callback invoked whenever the operation VA changes.

        Looks up the new operation name in the calibration data and applies each
        listed VA on the gun exciter component. If the operation mode has no
        calibration entry (e.g. "User defined"), the VAs are left unchanged.

        :param operation: the new value of the operation VA.
        """
        md = self._gun_exciter.getMetadata()
        calib = md.get(model.MD_CALIB)
        if not isinstance(calib, dict) or "operation" not in calib:
            logging.info("ebeam-gun-exciter MD_CALIB is missing or has no 'operation' key")
            return

        va_values = calib["operation"].get(operation)
        if va_values is None:
            logging.info("operation '%s' has no calibration entry, leaving VAs unchanged", operation)
            return

        logging.debug("Updating settings of ebeam-gun-exciter for operation '%s'", operation)
        for va_name, value in va_values.items():
            if not model.hasVA(self._gun_exciter, va_name):
                logging.info("operation '%s' references unknown VA '%s', skipping", operation, va_name)
                continue

            try:
                va = getattr(self._gun_exciter, va_name)
                va.value = value
                logging.debug("set %s = %s", va_name, value)
            except Exception:
                logging.info("failed to set %s = %s", va_name, value, exc_info=True)
