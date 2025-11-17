# -*- coding: utf-8 -*-
"""
Created on 20 Jan 2025

@author: Patrick Cleeve

Copyright © 2025 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import configparser
import logging
import os
from datetime import datetime

from odemis.gui.conf.file import CONF_PATH


# tmp flag for odemis advanced mode
# TODO: remove and replace once the licenced version is released
def get_license_enabled() -> dict:
    """
    Temporary function to get the license status.
    Allows to enable/disable without changing the code.
    """
    enabled  = False
    fibsem_enabled = False
    milling_enabled = False
    correlation_enabled = False
    expires_at = datetime(1970, 1, 1)  # default to 1970-01-01

    odemis_advanced_config = os.path.abspath(os.path.join(CONF_PATH, "odemis_advanced.config"))
    if os.path.exists(odemis_advanced_config):
        config = configparser.ConfigParser(interpolation=None)
        config.read(odemis_advanced_config)
        if "licence" not in config:
            config["licence"] = {}

        enabled = config.getboolean("licence", "enabled", fallback=False)
        # Sub-feature flags to disable some specific part of the odemis-advanced mode
        fibsem_enabled = enabled and config.getboolean("licence", "fibsem", fallback=True)
        milling_enabled = enabled and config.getboolean("licence", "milling", fallback=True)
        correlation_enabled = enabled and config.getboolean("licence", "correlation", fallback=True)

        # Decode expiry date as YYYY-MM-DD
        if "expires_at" in config["licence"]:
            try:
                expires_at = datetime.strptime(config["licence"]["expires_at"], "%Y-%m-%d")
            except ValueError:
                logging.error("Invalid date format in odemis_advanced.config: 'expires_at' must be in the format 'YYYY-MM-DD'")

        if expires_at < datetime.now():
            enabled = False
            fibsem_enabled = False
            milling_enabled = False
            correlation_enabled = False
            logging.warning("odemis-advanced licence has expired on %s", expires_at.strftime("%Y-%m-%d"))

    logging.debug(f"odemis-advanced mode is {'enabled' if enabled else 'disabled'}")
    return {"enabled": enabled,
            "fibsem": fibsem_enabled,
            "milling": milling_enabled,
            "correlation": correlation_enabled,
            "expires_at": expires_at.strftime("%Y-%m-%d")}

licences_enabled = get_license_enabled()
ODEMIS_ADVANCED_FLAG = licences_enabled["enabled"]
LICENCE_FIBSEM_ENABLED = licences_enabled["fibsem"]
LICENCE_MILLING_ENABLED = licences_enabled["milling"]
LICENCE_CORRELATION_ENABLED = licences_enabled["correlation"]
