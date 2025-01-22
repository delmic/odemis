import configparser
import logging

import os
from odemis.gui.conf.file import CONF_PATH


# tmp flag for odemis advanced mode
# TODO: remove and replace once the licenced version is released
def get_license_enabled() -> bool:
    """
    Temporary function to get the license status.
    Allows to enable/disable without changing the code.
    """
    enabled  = False
    fibsem_enabled = False
    milling_enabled = False
    correlation_enabled = False
    odemis_advanced_config = os.path.abspath(os.path.join(CONF_PATH, "odemis_advanced.config"))
    if os.path.exists(odemis_advanced_config):
        config = configparser.ConfigParser(interpolation=None)
        config.read(odemis_advanced_config)
        enabled = config["licence"].get("enabled", "False") == "True"
        fibsem_enabled = config["licence"].get("fibsem", "True") == "True"
        milling_enabled = config["licence"].get("milling", "True") == "True"
        correlation_enabled = config["licence"].get("correlation", "True") == "True"

    logging.debug(f"odemis-advanced mode is {'enabled' if enabled else 'disabled'}")
    return {"enabled": enabled,
            "fibsem": fibsem_enabled,
            "milling": milling_enabled,
            "correlation": correlation_enabled}

licences_enabled = get_license_enabled()
ODEMIS_ADVANCED_FLAG = licences_enabled["enabled"]
LICENCE_FIBSEM_ENABLED = licences_enabled["fibsem"]
LICENCE_MILLING_ENABLED = licences_enabled["milling"]
LICENCE_CORRELATION_ENABLED = licences_enabled["correlation"]
