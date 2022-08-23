# -*- coding: utf-8 -*-
"""
Created on 14 Jan 2013

@author: Rinze de Laat

Copyright © 2013-2016 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains all code needed for the access to and management of GUI
related configuration files.

"""
CONF_GENERAL = None
CONF_ACQUI = None
CONF_CHAMB = None
CONF_CALIB = None


def get_general_conf():
    global CONF_GENERAL
    if not CONF_GENERAL:
        from .file import GeneralConfig
        CONF_GENERAL = GeneralConfig()
    return CONF_GENERAL

def get_chamber_conf():
    """ Return the Chamber config object and create/read it first if it does not yet exist """
    global CONF_CHAMB
    if not CONF_CHAMB:
        from .file import ChamberConfig
        CONF_CHAMB = ChamberConfig()
    return CONF_CHAMB


def get_acqui_conf():
    """ Return the Acquisition config object and create/read it first if it does not yet exist """
    global CONF_ACQUI
    if not CONF_ACQUI:
        from .file import AcquisitionConfig
        CONF_ACQUI = AcquisitionConfig()
    return CONF_ACQUI


def get_calib_conf():
    """ Return the calibration config object and create/read it first if it does not yet exist """
    global CONF_CALIB
    if not CONF_CALIB:
        from .file import CalibrationConfig
        CONF_CALIB = CalibrationConfig()
    return CONF_CALIB
